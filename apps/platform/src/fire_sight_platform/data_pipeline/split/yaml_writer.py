"""写 Ultralytics 数据集 yaml。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping

import yaml


def write_dataset_yaml(
    path: Path,
    dataset_name: str,
    processed_root: Path,
    names: Mapping[int, str],
    meta: Dict | None = None,
) -> Path:
    """生成 data.yaml: path/train/val/test/names + fire_sight_meta 审计块。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "path": str(processed_root.resolve()),
        "train": str((processed_root / "images" / "train").resolve()),
        "val": str((processed_root / "images" / "val").resolve()),
        "test": str((processed_root / "images" / "test").resolve()),
        "names": {int(k): str(v) for k, v in names.items()},
        "fire_sight_meta": {
            "dataset": dataset_name,
            **(meta or {}),
        },
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
