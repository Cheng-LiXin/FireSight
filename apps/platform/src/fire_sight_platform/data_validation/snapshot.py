"""数据集快照:把 yaml + 磁盘状态装进只读结构,供所有 check 共享。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from fire_sight_platform.common.constants import IMAGE_EXTENSIONS
from fire_sight_platform.data_validation.registry import CheckContext


def _label_dir(data_root: Path, split: str, img_dir: Path) -> Path:
    """定位某个划分的标签目录。

    两种布局都要认，否则对「提供方切好划分」的数据集只会报「缺失标签 100%」
    ——那不是数据坏了，是这个函数只认 pipeline 的产物布局：

    - pipeline 产物（processed/）：`images/<split>` 与 `labels/<split>` 平级挂在 data_root 下
    - 提供方划分（raw/）：`images/` 与 `labels/` 是 split 目录下的两个兄弟
      （见 data/raw/D-Fire/data.yaml，split_strategy=provided）

    取第一个真实存在的目录。都不存在时返回 processed 那个，让检查项如实报出
    「标签目录不存在」，而不是把一个不存在的路径当成「空目录、零缺失」。
    """
    processed = data_root / "labels" / split
    if processed.is_dir():
        return processed
    sibling = img_dir.parent / "labels"
    if sibling.is_dir():
        return sibling
    return processed


def _resolve_dir(base: Path, value: object) -> Path:
    """把 yaml 里的一个路径值解成绝对路径。

    相对值按 `base`（数据集自己的 `path` 字段或 yaml 所在目录）解析，**不是**按
    进程的 CWD：`path: <abs>` + `train: train/images` 是 ultralytics/roboflow 的
    标准写法，按 CWD 解会指向一个不存在的目录，于是一份完好的数据集被判
    「没有任何图像样本」（2026-10-09 代码审查 R4）。
    """
    p = Path(str(value))
    return p if p.is_absolute() else (base / p)


def _split_dirs(data_root: Path, raw_value: object, split: str) -> List[Path]:
    """归一化某个划分的取值。

    ultralytics 允许 `train:` 是列表（多目录），此前直接把 list 丢给 `Path()`，
    抛出的 `TypeError` 还会逃出逐检查守卫、让整轮体检带 traceback 死掉
    （2026-10-09 代码审查 R6）。

    没有配置该划分时退回 `data_root/images/<split>`——这是 pipeline 产物的布局，
    但**不再**像以前那样在目录不存在时静默换一个路径：那样会把「配置写错了」
    藏起来，报告里只留下一个看不懂的空划分。目标目录是否存在由检查项如实报出。
    """
    if raw_value in (None, ""):
        return [data_root / "images" / split]
    values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
    dirs = [_resolve_dir(data_root, v) for v in values if v not in (None, "")]
    return dirs or [data_root / "images" / split]


def build_snapshot(yaml_path: Path) -> CheckContext:
    """从 data.yaml 构建质检上下文(一次扫描)。"""
    yaml_path = Path(yaml_path)
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    # `path` 也按同一纪律解析：相对值相对 yaml 所在目录，而不是 CWD。
    if payload.get("path"):
        data_root = _resolve_dir(yaml_path.parent, payload["path"])
    else:
        data_root = yaml_path.parent
    names_raw = payload.get("names") or {}
    if isinstance(names_raw, list):
        names = {i: n for i, n in enumerate(names_raw)}
    else:
        names = {int(k): v for k, v in names_raw.items()}

    splits: Dict[str, List[Path]] = {}
    label_files: Dict[str, List[Path]] = {}
    label_dirs: Dict[str, Path] = {}
    for split in ("train", "val", "test"):
        img_dirs = _split_dirs(data_root, payload.get(split), split)
        images: List[Path] = []
        for img_dir in img_dirs:
            images.extend(
                p for p in img_dir.glob("*") if p.suffix.lower() in IMAGE_EXTENSIONS
            )
        splits[split] = sorted(images)
        # 标签目录按第一个图像目录定位：提供方布局下 `labels/` 与 `images/` 是
        # 兄弟，多目录写法里每个目录各有自己的兄弟标签目录，这里认第一个。
        lbl_dir = _label_dir(data_root, split, img_dirs[0])
        label_dirs[split] = lbl_dir
        label_files[split] = sorted(lbl_dir.glob("*.txt")) if lbl_dir.is_dir() else []

    return CheckContext(
        yaml_path=yaml_path,
        data_root=data_root,
        names=names,
        splits=splits,
        label_files=label_files,
        label_dirs=label_dirs,
    )
