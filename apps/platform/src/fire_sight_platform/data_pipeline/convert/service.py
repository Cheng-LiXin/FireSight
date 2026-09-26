"""convert 服务层:按格式查注册表 → 调用转换函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from fire_sight_platform.data_pipeline.convert.registry import get_converter


def convert_data_to_yolo(
    source_format: str,
    source_dir: Path,
    images_dir: Path,
    labels_dir: Path,
    class_to_id: Dict[str, int],
) -> Tuple[int, int]:
    """把 source_dir 中的标注转换为 labels_dir 下的 YOLO txt。"""
    converter = get_converter(source_format)
    return converter(Path(source_dir), Path(images_dir), Path(labels_dir), class_to_id)
