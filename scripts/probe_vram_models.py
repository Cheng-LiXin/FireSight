"""实测各模型规模的显存开销，给显存守卫标定模型规模因子。

守卫的预算是 `batch × imgsz² × 模型规模因子`，其中因子就是这个脚本量出来的。
换模型规模（加 yolo26、换检测器、换显卡）时重跑它，把结果填进
`fire_sight_platform/training/vram.py` 的 `MODEL_FACTORS` 与 `MODEL_MEASUREMENTS`——
`tests/test_19_vram_guard.py` 里有一条测试会**从留档数据重算因子**，两者对不上就挂。

用法：

    # 默认测 yolo11n/s/m，batch 8 与 32
    .\\.venv\\Scripts\\python.exe scripts\\probe_vram_models.py

    # l/x 在 batch=32 就吃满 8 GB，得往下探
    .\\.venv\\Scripts\\python.exe scripts\\probe_vram_models.py --models yolo11l.pt,yolo11x.pt --batches 4,16

方法
----
峰值显存 ≈ 模型固有开销(权重/梯度/优化器状态) + 激活开销(∝ batch × imgsz²)。
对每个模型跑**两个 batch 点**线性拟合出常数项与斜率，因子取**斜率之比**——
常数项与模型规模无关地混在里面，只有斜率才是纯激活开销。

    yolo11n 65.1 MB/batch | yolo11s 118.3 | yolo11m 248.9 | yolo11l 317.9 | yolo11x 476.5

两点注意
--------
1. **饱和的点不能用**。yolo11m 在 batch=32 × imgsz=480 报到 8186 MiB，已经是整张卡
   （8187.5 MiB），那个「需求」被截断了。判据：某个点的峰值贴近显卡总量就换低 batch 重测。
2. 测量口径要与真实训练一致：不传 amp（ultralytics 在 CUDA 上默认开 AMP）、不传
   optimizer，`cache=False`、`workers=0`（排除 DataLoader 干扰）。

`if __name__ == "__main__":` 守卫是必需的（Windows + torch spawn，见 devlog）。
"""

from __future__ import annotations

import gc
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODELS = ["yolo11n.pt", "yolo11s.pt", "yolo11m.pt"]
BATCHES = [8, 32]
IMGSZ = 480
DATASET = "D-Fire/raw"
# 14122 × 0.003 ≈ 42 张：够撑满 batch=32 的一批，又不必等整个数据集
FRACTION = 0.003


def _args() -> tuple:
    """允许从命令行覆盖模型与 batch 点：m 在 batch=32 就吃满 8 GB，l/x 得往下探。"""
    import argparse

    p = argparse.ArgumentParser(description="实测模型规模因子（改 vram.py 前重跑）")
    p.add_argument("--models", default=",".join(MODELS))
    p.add_argument("--batches", default=",".join(str(b) for b in BATCHES))
    p.add_argument("--fraction", type=float, default=FRACTION)
    a = p.parse_args()
    return a.models.split(","), [int(b) for b in a.batches.split(",")], a.fraction


def measure(model_name: str, batch: int, data_yaml: str, fraction: float) -> float:
    """跑一轮真实训练，返回本次的峰值显存 MB。"""
    import torch
    from ultralytics import YOLO

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    model = YOLO(model_name)
    model.train(
        data=str(data_yaml),
        epochs=1,
        imgsz=IMGSZ,
        batch=batch,
        fraction=fraction,
        workers=0,
        cache=False,
        device=0,
        val=False,
        plots=False,
        project=str(ROOT / ".runtime" / "vram_probe"),
        name=f"{Path(model_name).stem}-b{batch}",
        exist_ok=True,
        verbose=False,
    )
    peak = torch.cuda.max_memory_allocated() / 1024 / 1024

    del model
    gc.collect()
    torch.cuda.empty_cache()
    return peak


def main() -> int:
    from fire_sight_platform.common.runtime_env import apply_runtime_env, pin_ultralytics_paths
    from fire_sight_platform.training.service import _resolve_dataset_yaml

    models, batches, fraction = _args()
    apply_runtime_env()
    data_yaml = str(_resolve_dataset_yaml(DATASET))
    pin_ultralytics_paths()

    results: dict = {}
    for name in models:
        results[name] = {}
        for batch in batches:
            try:
                peak = measure(name, batch, data_yaml, fraction)
                results[name][batch] = round(peak, 1)
                print(f"  {name:<14} batch={batch:<3} 峰值 {peak:7.1f} MB", flush=True)
            except Exception as exc:  # noqa: BLE001 - 单个点失败不该毁掉整轮
                results[name][batch] = None
                print(f"  {name:<14} batch={batch:<3} 失败: {type(exc).__name__}: {exc}", flush=True)

    print("\nRAW " + json.dumps(results), flush=True)
    print(
        "\n把斜率之比填进 vram.py 的 MODEL_FACTORS，原始峰值填进 MODEL_MEASUREMENTS，\n"
        "然后跑 pytest tests/test_19_vram_guard.py —— 它会从留档数据重算因子来校验。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
