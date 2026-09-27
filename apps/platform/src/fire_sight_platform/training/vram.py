"""显存预算守卫:按 GPU 总显存给出 batch × imgsz 的上限。

峰值显存大致正比于 `batch × imgsz²`,所以「像素单位」这一个量就能横向比较
不同 batch / imgsz 组合的相对开销。比例常数由本机**唯一一个跑完且指标可信**
的现场标定:

    20260926-190635-D-Fire / train-yolo11n,batch=72 × imgsz=480,
    RTX 4060 Laptop 8 GB(8188 MB),mAP@50 = 0.745

它同时是已知的上限,因此按它反推每 MB 显存的像素预算,超出即拒绝。

**这是启发式,不是精确测量**:换模型规模(n→s/m)、换 GPU 架构、改 workers
或 cache 都会让它偏。宁可保守拒绝,也不要再让一次十分钟的训练在 OOM 上白跑
——20260927 那次 `batch=48 × imgsz=640`(19.66M 像素单位,比标定点高 18%)
就是这么废掉的。
"""

from __future__ import annotations

from dataclasses import dataclass

# 标定点:本机验证过的最大像素单位
REFERENCE_BATCH = 72
REFERENCE_IMGSZ = 480
REFERENCE_VRAM_MB = 8188
REFERENCE_PX = REFERENCE_BATCH * REFERENCE_IMGSZ**2
REFERENCE_NOTE = (
    f"batch={REFERENCE_BATCH} × imgsz={REFERENCE_IMGSZ}"
    f"(20260926-190635-D-Fire,mAP@50 0.745)"
)


def px_budget(total_mb: int) -> int:
    """该显存能承受的 `batch × imgsz²` 上限,按标定点线性外推。"""
    if total_mb <= 0:
        raise ValueError("显存容量必须为正")
    return total_mb * REFERENCE_PX // REFERENCE_VRAM_MB


def cost(imgsz: int, batch: int) -> int:
    """一组配置的像素单位开销。"""
    return batch * imgsz * imgsz


def max_batch(imgsz: int, total_mb: int) -> int:
    """给定 imgsz 时本机允许的最大 batch。至少给 1——否则任何配置都起不来,
    连「换个更小的图也跑不动」这种极端情况都该让用户自己看见失败。"""
    if imgsz <= 0:
        raise ValueError("imgsz 必须为正")
    return max(1, px_budget(total_mb) // (imgsz * imgsz))


@dataclass(frozen=True)
class VramVerdict:
    ok: bool
    total_mb: int
    px_budget: int
    cost: int
    max_batch: int
    message: str = ""


def _m(value: int) -> str:
    """像素单位读起来太长,收成 M。"""
    return f"{value / 1_000_000:.2f}M"


def check(imgsz: int, batch: int, total_mb: int) -> VramVerdict:
    """判断一组 batch/imgsz 是否超预算。"""
    budget = px_budget(total_mb)
    need = cost(imgsz, batch)
    limit = max_batch(imgsz, total_mb)
    if need <= budget:
        return VramVerdict(True, total_mb, budget, need, limit)

    message = (
        f"batch={batch} × imgsz={imgsz} 需要 {_m(need)} 像素单位,"
        f"超过本机 {total_mb} MB 显存的预算 {_m(budget)}。"
        f"本机已验证的上限是 {REFERENCE_NOTE};"
        f"imgsz={imgsz} 时 batch 最多 {limit}。"
    )
    return VramVerdict(False, total_mb, budget, need, limit, message)
