"""数据集快照:把 yaml + 磁盘状态装进只读结构,供所有 check 共享。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from fire_sight_platform.data_validation.registry import CheckContext


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
    for split in ("train", "val", "test"):
        img_dir = Path(payload.get(split) or (data_root / "images" / split))
        if not img_dir.is_dir():
            img_dir = data_root / "images" / split
        images = sorted(p for p in img_dir.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"})
        splits[split] = images
        lbl_dir = data_root / "labels" / split
        label_files[split] = sorted(lbl_dir.glob("*.txt")) if lbl_dir.is_dir() else []

    return CheckContext(
        yaml_path=yaml_path,
        data_root=data_root,
        names=names,
        splits=splits,
        label_files=label_files,
    )
