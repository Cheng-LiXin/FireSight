"""数据集快照:把 yaml + 磁盘状态装进只读结构,供所有 check 共享。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from fire_sight_platform.data_validation.registry import CheckContext

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webm", ".webp"}


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


def build_snapshot(yaml_path: Path) -> CheckContext:
    """从 data.yaml 构建质检上下文(一次扫描)。"""
    yaml_path = Path(yaml_path)
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    data_root = Path(payload.get("path") or yaml_path.parent)
    names_raw = payload.get("names") or {}
    if isinstance(names_raw, list):
        names = {i: n for i, n in enumerate(names_raw)}
    else:
        names = {int(k): v for k, v in names_raw.items()}

    splits: Dict[str, List[Path]] = {}
    label_files: Dict[str, List[Path]] = {}
    label_dirs: Dict[str, Path] = {}
    for split in ("train", "val", "test"):
        img_dir = Path(payload.get(split) or (data_root / "images" / split))
        if not img_dir.is_dir():
            img_dir = data_root / "images" / split
        images = sorted(p for p in img_dir.glob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
        splits[split] = images
        lbl_dir = _label_dir(data_root, split, img_dir)
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
