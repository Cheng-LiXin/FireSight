"""可用模型清单：训练界面「模型」下拉的数据来源。

为什么要有这个模块，而不是让前端自己写死几个名字：
**哪些模型真能在本机跑，取决于两件事——权重文件在不在，以及显存够不够。**
把清单放在这里，守卫（`vram.py` 的规模因子）与界面就共用同一份真相，
不会出现「下拉里能选、一点就 400」这种事。

权重的落点由 `runtime_env` 钉在 `.runtime/ultralytics/weights/`（改道自 C 盘，
见该模块的文档）。ultralytics 自己会在缺失时联网下载，但**本机离线或没网时
就会失败**，所以这里如实标出 `present`，让用户自己决定要不要选它。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from fire_sight_platform.common.paths import RUNTIME_ULTRALYTICS_DIR
from fire_sight_platform.training import vram

# 候选家族，按「由小到大」列出——这是本项目实验过/要实验的顺序。
# 只列 Ultralytics 官方有预训练权重的检测模型；键名即权重主名。
#   yolo26n 是本机 AMP 自检时下下来的（vram.py 文档里提到的那个），
#   留着是因为它确实存在、也确实能训，但**没有显存实测数据**，会按未知模型处理。
FAMILIES: tuple[str, ...] = (
    "yolo11n",
    "yolo11s",
    "yolo11m",
    "yolo11l",
    "yolo11x",
    "yolo26n",
)

# 界面上的中文说明。没说法的就不编——宁可空着。
_BLURB = {
    "yolo11n": "最省显存，本项目的标定基准（现役对照模型）",
    "yolo11s": "实测同预算下 mAP50 比 n 高约 0.021，训练 1.72×",
    "yolo11m": "显存吃紧：8 GB 卡上 imgsz=640 时批量只有个位数",
    "yolo11l": "8 GB 卡基本跑不动 imgsz=640，慎选",
    "yolo11x": "8 GB 卡跑不动，列出来只是为了让清单完整",
    "yolo26n": "较新的 n 规模对照，本机没有实测显存数据（按 m 规模保守估）",
}


@dataclass(frozen=True)
class ModelOption:
    """一个可选的预训练权重。"""

    #: 权重主名，如 `yolo11s`（`vram.model_stem` 的口径）
    stem: str
    #: 传给后端的值，如 `yolo11s.pt`
    name: str
    #: 权重文件是否已在本机（缺失时 ultralytics 会尝试联网下载）
    present: bool
    #: 本地文件大小（MB），缺失时为 None
    size_mb: Optional[float]
    #: 显存规模因子是否有**实测**依据（False 表示按未知模型保守估）
    measured: bool
    factor: float
    note: str


def weights_dir():
    """预训练权重的落点（与 runtime_env 的钉定保持一致）。"""
    return RUNTIME_ULTRALYTICS_DIR / "weights"


def weight_path(stem: str):
    return weights_dir() / f"{stem}.pt"


def list_options() -> List[ModelOption]:
    """全部候选模型，附本机是否已有权重。

    只读文件系统，不联网、不 import torch——这个函数会被 `/api/train/models`
    每次调用，必须是毫秒级的。
    """
    out: List[ModelOption] = []
    for stem in FAMILIES:
        path = weight_path(stem)
        size_mb: Optional[float] = None
        present = False
        try:
            if path.is_file():
                present = True
                size_mb = round(path.stat().st_size / 1024 / 1024, 1)
        except OSError:
            # 读不到就当没有：界面标「需下载」，用户选了会得到一次明确的失败，
            # 总好过在这里抛异常让整个下拉空掉。
            present = False

        factor, measured = vram.model_factor(stem)
        out.append(
            ModelOption(
                stem=stem,
                name=f"{stem}.pt",
                present=present,
                size_mb=size_mb,
                measured=measured,
                factor=factor,
                note=_BLURB.get(stem, ""),
            )
        )
    return out
