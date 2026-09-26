"""COCO JSON → YOLO txt。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from fire_sight_platform.data_pipeline.convert.converters._geometry import format_yolo_line, normalize_box
from fire_sight_platform.data_pipeline.convert.registry import register_converter


@register_converter("coco")
def convert_coco(
    source_dir: Path,
    images_dir: Path,
    labels_dir: Path,
    class_to_id: Dict[str, int],
) -> Tuple[int, int]:
    """读取 source_dir 下 COCO json,写出 YOLO 标签。"""
    labels_dir.mkdir(parents=True, exist_ok=True)
    candidates = ["annotations.json", "coco.json", "instances.json"]
    json_path = next((source_dir / c for c in candidates if (source_dir / c).exists()), None)
    if json_path is None:
        raise FileNotFoundError(f"{source_dir} 下未找到 COCO json {candidates}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    images = {img["id"]: img for img in data.get("images", [])}
    cats = {c["id"]: c["name"] for c in data.get("categories", [])}

    by_image: Dict[int, List[str]] = {}
    for ann in data.get("annotations", []):
        img = images.get(ann.get("image_id"))
        if img is None:
            continue
        name = cats.get(ann.get("category_id"), "")
        if name not in class_to_id:
            raise ValueError(f"COCO 未知类别 {name!r}")
        x, y, w, h = ann["bbox"]
        box = normalize_box(x, y, x + w, y + h, float(img["width"]), float(img["height"]))
        by_image.setdefault(img["id"], []).append(format_yolo_line(class_to_id[name], box))

    for img_id, img in images.items():
        stem = Path(img["file_name"]).stem
        lines = by_image.get(img_id, [])
        (labels_dir / f"{stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
    return len(images), sum(len(v) for v in by_image.values())
