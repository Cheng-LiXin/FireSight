"""显存预算守卫:按 GPU 总显存与模型规模给出 batch × imgsz 的上限。

峰值显存 ≈ **模型固有开销**(权重/梯度/优化器状态) + **激活开销**(∝ batch × imgsz²)。
所以预算以「像素单位」`batch × imgsz²` 计量,再乘一个模型规模因子。

标定点
------
比例常数来自本机**唯一一个跑完且指标可信**的现场:

    20260926-190635-D-Fire / train-yolo11n,batch=72 × imgsz=480,
    RTX 4060 Laptop 8 GB(8188 MiB),mAP@50 = 0.745

模型规模因子
------------
2026-09-27 实测:对每个规模的模型各跑真实训练(口径与正式训练一致——不传 amp
让 ultralytics 用默认 AMP、`cache=False`、`workers=0`),取两个 batch 点线性拟合,
用**斜率之比**(纯激活开销,不含模型固有开销)作因子。

    yolo11n  65.1 MB/batch   1.00   ← 标定点
    yolo11s 118.3            1.82
    yolo11m 248.9            3.83
    yolo11l 317.9            4.89
    yolo11x 476.5            7.32

本机实测真实上限(imgsz=480):n≈123 / s≈67 / m≈31 / l≈23 / x≈15。
守卫在各档给出的上限都低于实测值,保守度约 1.7 倍——这个余量继承自标定点,
因为标定假设那次 batch=72 用满了 8 GB,而实测它只用约 4.8 GB。
**宁可保守拒绝,也不要再让一次十分钟的训练白跑。**

已知偏差
--------
- 换 GPU 架构、改 workers / cache、多卡都会让常数偏。
- 本机驱动开着**系统内存回退**:实测 yolo11x 在 batch=16 报到 8490 MiB,
  超过显存总量 8187.5 MiB。也就是说超预算不一定 OOM,而是走 PCIe 变得极慢
  ——这类失败更难察觉,更需要这道守卫。
- 未知模型名(含从归档 `best.pt` 继续训练)按 `UNKNOWN_MODEL_FACTOR` 处理,
  `check()` 会标 `measured=False`,调用方应把这一点告诉用户。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# 标定点:本机验证过的最大像素单位
REFERENCE_BATCH = 72
REFERENCE_IMGSZ = 480
REFERENCE_VRAM_MB = 8188
REFERENCE_PX = REFERENCE_BATCH * REFERENCE_IMGSZ**2
REFERENCE_MODEL = "yolo11n"
REFERENCE_NOTE = (
    f"batch={REFERENCE_BATCH} × imgsz={REFERENCE_IMGSZ}"
    f"(20260926-190635-D-Fire,mAP@50 0.745)"
)

# 模型规模因子:实测激活开销相对 yolo11n 的倍数。键为权重文件的主名。
MODEL_FACTORS: Dict[str, float] = {
    "yolo11n": 1.00,
    "yolo11s": 1.82,
    "yolo11m": 3.83,
    "yolo11l": 4.89,
    "yolo11x": 7.32,
}

# 未知模型按已知的 m 规模处理。比不设限安全:不设限等于把 yolo11m 当成 n 放行,
# 实测那会超预算 2.2 倍。
UNKNOWN_MODEL_FACTOR = MODEL_FACTORS["yolo11m"]

# 实测原始数据(MB 峰值显存),留档以便复核因子怎么来的
MODEL_MEASUREMENTS: Dict[str, Dict[int, float]] = {
    "yolo11n": {8: 672.4, 32: 2235.8},
    "yolo11s": {8: 1202.7, 32: 4041.4},
    "yolo11m": {4: 1361.1, 16: 4347.9},  # 32 处已贴满卡,取低 batch 拟合
    "yolo11l": {4: 1709.9, 16: 5524.7},
    "yolo11x": {4: 2772.3, 16: 8490.2},
}
MEASURED_AT_IMGSZ = 480
MEASURED_ON = "RTX 4060 Laptop 8 GB (8188 MiB), 2026-09-27"


def model_stem(model: str) -> str:
    """从权重名或路径取主名:`yolo11s.pt` / `a/b/yolo11s.pt` -> `yolo11s`。"""
    name = str(model or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-3] if name.lower().endswith(".pt") else name


def model_factor(model: str = REFERENCE_MODEL) -> Tuple[float, bool]:
    """返回 (因子, 是否有实测依据)。

    未知模型一律按 m 规模估,并由第二个返回值标明它只是估计——上层要把这件事
    说出来,不能让人以为所有模型都实测过。
    """
    stem = model_stem(model)
    if stem in MODEL_FACTORS:
        return MODEL_FACTORS[stem], True
    return UNKNOWN_MODEL_FACTOR, False


def px_budget(total_mb: int) -> int:
    """该显存能承受的 `batch × imgsz²` 上限,按标定点线性外推(不含模型因子)。"""
    if total_mb <= 0:
        raise ValueError("显存容量必须为正")
    return total_mb * REFERENCE_PX // REFERENCE_VRAM_MB


def cost(imgsz: int, batch: int, model: str = REFERENCE_MODEL) -> int:
    """一组配置的等效像素单位开销(已乘模型规模因子)。"""
    factor, _ = model_factor(model)
    return int(batch * imgsz * imgsz * factor)


def max_batch(imgsz: int, total_mb: int, model: str = REFERENCE_MODEL) -> int:
    """给定 imgsz 与模型时本机允许的最大 batch。至少给 1——否则任何配置都起不来,
    连「换个更小的图也跑不动」这种极端情况都该让用户自己看见失败。"""
    if imgsz <= 0:
        raise ValueError("imgsz 必须为正")
    factor, _ = model_factor(model)
    per_sample = imgsz * imgsz * factor
    return max(1, int(px_budget(total_mb) // per_sample))


@dataclass(frozen=True)
class VramVerdict:
    ok: bool
    total_mb: int
    px_budget: int
    cost: int
    max_batch: int
    model: str = REFERENCE_MODEL
    factor: float = 1.0
    #: 该模型规模是否有实测依据。False 表示上限是估的,提示语要说明。
    measured: bool = True
    message: str = ""


def _m(value: float) -> str:
    """像素单位读起来太长,收成 M。"""
    return f"{value / 1_000_000:.2f}M"


def check(
    imgsz: int,
    batch: int,
    total_mb: int,
    model: str = REFERENCE_MODEL,
) -> VramVerdict:
    """判断一组 batch/imgsz/模型 是否超预算。"""
    budget = px_budget(total_mb)
    factor, measured = model_factor(model)
    stem = model_stem(model)
    need = cost(imgsz, batch, model)
    limit = max_batch(imgsz, total_mb, model)
    base = dict(
        total_mb=total_mb,
        px_budget=budget,
        cost=need,
        max_batch=limit,
        model=stem,
        factor=factor,
        measured=measured,
    )

    if need <= budget:
        return VramVerdict(ok=True, **base)

    size_note = (
        f"模型 {stem} 的显存开销约为 yolo11n 的 {factor:g} 倍(实测)"
        if measured
        else (
            f"模型 {stem} 没有实测数据,按 yolo11m 的 {factor:g} 倍估算"
            "——参数更多的话请再留余量"
        )
    )
    message = (
        f"batch={batch} × imgsz={imgsz} 在 {stem} 上需要 {_m(need)} 等效像素单位,"
        f"超过本机 {total_mb} MB 显存的预算 {_m(budget)}。"
        f"{size_note};"
        f"本机已验证的上限是 {REFERENCE_NOTE};"
        f"imgsz={imgsz} 时 batch 最多 {limit}。"
    )
    return VramVerdict(ok=False, message=message, **base)
