"""可用模型清单：训练界面「模型」下拉的数据来源。

为什么要有这个模块，而不是让前端自己写死几个名字：
**哪些模型真能在本机跑，取决于两件事——权重文件在不在，以及显存够不够。**
把清单放在这里，守卫（`vram.py` 的规模因子）与界面就共用同一份真相，
不会出现「下拉里能选、一点就 400」这种事。

权重的落点由 `runtime_env` 钉在 `.runtime/ultralytics/weights/`（改道自 C 盘，
见该模块的文档）。ultralytics 自己会在缺失时联网下载，但**本机离线或没网时
就会失败**，所以这里如实标出 `present`，让用户自己决定要不要选它。

**「本机有没有」要认两个落点**：受管目录，以及仓库根。后者不是历史包袱而是
ultralytics 的真实行为——它解析裸名时先看当前工作目录（见
`ultralytics.utils.downloads.attempt_download_asset`），而训练子进程的 CWD 正是
仓库根。只认受管目录会把根目录里躺着的权重标成「需下载」，连带把默认模型顶到
另一个上去（`6d56afb` 引入下拉时就是这么错到现在的）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fire_sight_platform.common.paths import ROOT_DIR, RUNTIME_ULTRALYTICS_DIR
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
    size_mb: float | None
    #: 显存规模因子是否有**实测**依据（False 表示按未知模型保守估）
    measured: bool
    factor: float
    note: str


def weights_dir():
    """预训练权重的受管落点（与 runtime_env 的钉定保持一致）。"""
    return RUNTIME_ULTRALYTICS_DIR / "weights"


def resolve_weight(name: str) -> Path | None:
    """把权重名（`yolo11s.pt` 或一条路径）解析成本机上真实存在的文件。

    顺序与 ultralytics 自己的查找对齐，否则界面说「已就绪」而训练进程报
    「找不到权重」：

    1. 绝对路径——就认它；
    2. 带目录的相对路径——先按调用方的工作目录，再按仓库根；
    3. 裸名——受管目录 `.runtime/ultralytics/weights/`（即 `SETTINGS["weights_dir"]`），
       再仓库根。仓库根这条不是历史包袱：`attempt_download_asset` 先看 CWD，
       而训练子进程的 CWD 正是仓库根。

    返回的一定是**绝对路径**，调用方可以直接喂给 `YOLO()`，不必再猜 CWD。
    只读文件系统，不联网、不 import torch。
    """
    path = Path(name).expanduser()
    if path.is_absolute():
        candidates: list[Path] = [path]
    elif path.parent != Path("."):
        candidates = [path, ROOT_DIR / path, weights_dir() / path.name, ROOT_DIR / path.name]
    else:
        candidates = [weights_dir() / path.name, ROOT_DIR / path.name]

    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue
    return None


def training_weight(name: str) -> Path:
    """训练时该喂给 `YOLO()` 的权重路径——**一定是一条绝对路径**。

    找到了就用找到的那一份；没找到就把落点钉在受管目录里，让 ultralytics 下载
    时写进去。不钉的话它按 `safe_download` 的默认行为落到 CWD（＝仓库根），
    权重从此散在两个地方，界面也就又看不见了——这个 bug 就是这么来的。
    """
    found = resolve_weight(name)
    if found is not None:
        return found

    path = Path(name).expanduser()
    if path.is_absolute():
        return path
    return weights_dir() / path.name


def list_options() -> list[ModelOption]:
    """全部候选模型，附本机是否已有权重。

    只读文件系统，不联网、不 import torch——这个函数会被 `/api/train/models`
    每次调用，必须是毫秒级的。
    """
    out: list[ModelOption] = []
    for stem in FAMILIES:
        path = resolve_weight(f"{stem}.pt")
        size_mb: float | None = None
        present = False
        try:
            if path is not None:
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
