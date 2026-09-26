"""Pascal VOC XML → YOLO txt。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple

from fire_sight_platform.data_pipeline.convert.converters._geometry import format_yolo_line, is_degenerate, normalize_box
from fire_sight_platform.data_pipeline.convert.registry import register_converter


@register_converter("pascal_voc")
def convert_pascal_voc(
    source_dir: Path,
    images_dir: Path,
    labels_dir: Path,
    class_to_id: Dict[str, int],
) -> Tuple[int, int]:
    """把 source_dir 下的 VOC xml 转成 labels_dir 下的 YOLO txt。"""
    labels_dir.mkdir(parents=True, exist_ok=True)
    xml_files = sorted(source_dir.glob("*.xml"))
    box_total = 0
    for xml_path in xml_files:
        root = ET.parse(xml_path).getroot()
        size = root.find("size")
        width = float(size.findtext("width") or 0)
        height = float(size.findtext("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError(f"{xml_path.name}: size 宽高非法")

        lines: List[str] = []
        for obj in root.findall("object"):
            name = (obj.findtext("name") or "").strip()
            if name not in class_to_id:
                raise ValueError(f"{xml_path.name}: 未知类别 {name!r}")
            bnd = obj.find("bndbox")
            box = normalize_box(
                float(bnd.findtext("x1")),
                float(bnd.findtext("y1")),
                float(bnd.findtext("x2")),
                float(bnd.findtext("y2")),
                width,
                height,
            )
            if is_degenerate(box[2], box[3]):
                continue
            lines.append(format_yolo_line(class_to_id[name], box))
        (labels_dir / f"{xml_path.stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        box_total += len(lines)
    return len(xml_files), box_total
