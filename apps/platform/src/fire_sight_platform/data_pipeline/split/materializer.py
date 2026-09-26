"""物化:按 split 把 (图,标签) 对复制到 processed/。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class SplitSourceDirs:
    images: Path
    labels: Path


@dataclass(frozen=True)
class SplitOutputDirs:
    root: Path

    def images(self, split: str) -> Path:
        return self.root / "images" / split

    def labels(self, split: str) -> Path:
        return self.root / "labels" / split


def _copy_pair(
    src_images: Path,
    src_labels: Path,
    dst_images: Path,
    dst_labels: Path,
    stem: str,
) -> None:
    dst_images.mkdir(parents=True, exist_ok=True)
    dst_labels.mkdir(parents=True, exist_ok=True)
    img = None
    for ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        candidate = src_images / f"{stem}{ext}"
        if candidate.exists():
            img = candidate
            break
    if img is None:
        raise FileNotFoundError(f"缺少图像: {stem}")
    shutil.copy2(img, dst_images / img.name)
    src_label = src_labels / f"{stem}.txt"
    if src_label.exists():
        shutil.copy2(src_label, dst_labels / f"{stem}.txt")
    else:
        (dst_labels / f"{stem}.txt").write_text("", encoding="utf-8")


def materialize(
    splits: Dict[str, List[str]],
    source: SplitSourceDirs,
    output: SplitOutputDirs,
) -> Dict[str, int]:
    """按冻结划分复制样本,返回各 split 样本数。可重复执行(先清空目标 split)。"""
    counts: Dict[str, int] = {}
    for split, stems in splits.items():
        img_out = output.images(split)
        lbl_out = output.labels(split)
        if img_out.exists():
            shutil.rmtree(img_out)
        if lbl_out.exists():
            shutil.rmtree(lbl_out)
        for stem in stems:
            _copy_pair(source.images, source.labels, img_out, lbl_out, stem)
        counts[split] = len(stems)
    return counts
