#!/usr/bin/env python
"""生成合成迷你数据集(开发/单测用,不依赖外网)。

用法:
    python scripts/make_demo_dataset.py --format yolo --name demo_fire
    python scripts/make_demo_dataset.py --format pascal_voc --name demo_voc
    python scripts/make_demo_dataset.py --format coco --name demo_coco
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

from fire_sight_platform.common import paths as paths_mod

CLASSES = ["flame", "smoke"]
COLORS = {"flame": (220, 80, 40), "smoke": (120, 120, 120)}


def _make_image(path: Path, boxes, size=(320, 240)) -> None:
    img = Image.new("RGB", size, (30, 30, 40))
    draw = ImageDraw.Draw(img)
    for name, x1, y1, x2, y2 in boxes:
        draw.rectangle([x1, y1, x2, y2], outline=COLORS[name], width=3)
        draw.ellipse([x1 + 8, y1 + 8, x2 - 8, y2 - 8], fill=COLORS[name])
    img.save(path)


def build(format: str, name: str, n: int = 12, out_root: Path | None = None) -> Path:
    root = Path(out_root or paths_mod.RAW_DATA_DIR) / name
    images = root / "images"
    ann = root / "annotations"
    images.mkdir(parents=True, exist_ok=True)
    ann.mkdir(parents=True, exist_ok=True)

    samples = []
    for i in range(n):
        boxes = []
        # 1-2 boxes per image
        if i % 3 != 0:
            boxes.append(("flame", 40 + i * 3, 30, 120 + i * 3, 110))
        if i % 2 == 0:
            boxes.append(("smoke", 150, 80 + (i % 5) * 4, 260, 180))
        samples.append((i, boxes))

    for i, boxes in samples:
        stem = f"img_{i:03d}"
        _make_image(images / f"{stem}.png", boxes)
        if format == "yolo":
            lines = []
            for name_, x1, y1, x2, y2 in boxes:
                cx = ((x1 + x2) / 2) / 320
                cy = ((y1 + y2) / 2) / 240
                w = (x2 - x1) / 320
                h = (y2 - y1) / 240
                lines.append(f"{CLASSES.index(name_)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            (ann / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        elif format == "pascal_voc":
            annotation = ET.Element("annotation")
            size = ET.SubElement(annotation, "size")
            ET.SubElement(size, "width").text = "320"
            ET.SubElement(size, "height").text = "240"
            ET.SubElement(size, "depth").text = "3"
            for name_, x1, y1, x2, y2 in boxes:
                obj = ET.SubElement(annotation, "object")
                ET.SubElement(obj, "name").text = name_
                bnd = ET.SubElement(obj, "bndbox")
                ET.SubElement(bnd, "x1").text = str(x1)
                ET.SubElement(bnd, "y1").text = str(y1)
                ET.SubElement(bnd, "x2").text = str(x2)
                ET.SubElement(bnd, "y2").text = str(y2)
            ET.ElementTree(annotation).write(ann / f"{stem}.xml", encoding="utf-8", xml_declaration=True)
        elif format == "coco":
            pass

    if format == "coco":
        images_json = []
        annotations = []
        categories = [{"id": i + 1, "name": n_} for i, n_ in enumerate(CLASSES)]
        ann_id = 1
        for i, boxes in samples:
            stem = f"img_{i:03d}"
            images_json.append({"id": i + 1, "file_name": f"{stem}.png", "width": 320, "height": 240})
            for name_, x1, y1, x2, y2 in boxes:
                annotations.append({
                    "id": ann_id,
                    "image_id": i + 1,
                    "category_id": CLASSES.index(name_) + 1,
                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                })
                ann_id += 1
        (ann / "annotations.json").write_text(
            json.dumps({"images": images_json, "annotations": annotations, "categories": categories}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"demo dataset ready: {root} format={format} n={n}")
    return root


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", default="yolo", choices=["yolo", "pascal_voc", "coco"])
    ap.add_argument("--name", default="demo_fire")
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()
    build(args.format, args.name, args.n)


if __name__ == "__main__":
    main()
