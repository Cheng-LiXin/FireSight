"""YOLO → YOLO(直通 + 校验)。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from fire_sight_platform.data_pipeline.convert.converters._geometry import format_yolo_line
from fire_sight_platform.data_pipeline.convert.registry import register_converter


@register_converter("yolo")
def convert_yolo(
    source_dir: Path,
    images_dir: Path,
    labels_dir: Path,
    class_to_id: Dict[str, int],
) -> Tuple[int, int]:
    """复制并规范化已有 YOLO 标签。"""
    labels_dir.mkdir(parents=True, exist_ok=True)
    valid_ids = set(class_to_id.values())
    src_files = sorted(source_dir.glob("*.txt"))
    box_total = 0
    for src in src_files:
        out_lines = []
        for line_no, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                raise ValueError(f"{src.name}:{line_no} 字段不足 5")
            cid = int(float(parts[0]))
            if cid not in valid_ids:
                raise ValueError(f"{src.name}:{line_no} 非法 class_id={cid}")
            box = tuple(float(x) for x in parts[1:5])
            if any(v < 0 or v > 1 for v in box):
                raise ValueError(f"{src.name}:{line_no} 归一化坐标越界: {box}")
            out_lines.append(format_yolo_line(cid, box))
        (labels_dir / src.name).write_text(
            "\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8"
        )
        box_total += len(out_lines)
    return len(src_files), box_total
