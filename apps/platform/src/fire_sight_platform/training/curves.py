"""解析 ultralytics 的 results.csv，产出可直接绘图的序列。

纯函数，便于单测。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# 序列里允许 None：某一轮某列缺失时留断点，前端据此断开折线而不是画到 0。
Series = List[Optional[float]]

# results.csv 的列名 → (分组, 前端用的键)。
# 列名里的 "(B)" 是 ultralytics 的标注、随版本可能变，故集中在此映射。
_COLUMN_MAP: Dict[str, tuple] = {
    "epoch": ("axis", "epoch"),
    "train/box_loss": ("losses", "train_box"),
    "train/cls_loss": ("losses", "train_cls"),
    "train/dfl_loss": ("losses", "train_dfl"),
    "val/box_loss": ("losses", "val_box"),
    "val/cls_loss": ("losses", "val_cls"),
    "val/dfl_loss": ("losses", "val_dfl"),
    "metrics/precision(B)": ("metrics", "precision"),
    "metrics/recall(B)": ("metrics", "recall"),
    "metrics/mAP50(B)": ("metrics", "mAP50"),
    "metrics/mAP50-95(B)": ("metrics", "mAP50_95"),
    "lr/pg0": ("lr", "pg0"),
    "lr/pg1": ("lr", "pg1"),
    "lr/pg2": ("lr", "pg2"),
}

_GROUPS = ("losses", "metrics", "lr")


@dataclass
class Curves:
    """一条训练曲线。各分组内的键与前端图表配置一一对应。"""

    epochs: List[int] = field(default_factory=list)
    losses: Dict[str, Series] = field(default_factory=dict)
    metrics: Dict[str, Series] = field(default_factory=dict)
    lr: Dict[str, Series] = field(default_factory=dict)
    # 原始列名，便于排查「这列是什么」
    columns: List[str] = field(default_factory=list)

    @property
    def total_epochs(self) -> int:
        return len(self.epochs)

    @property
    def empty(self) -> bool:
        return not self.epochs


def _to_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def read_results_csv(path: Path | str) -> Curves:
    """读 results.csv。无法识别的列一律跳过，不抛异常。

    训练中断时 CSV 可能只写了一半，所以逐行独立容错：某个值不可解析就记 None，
    而不是让整个接口失败。
    """
    path = Path(path)
    curves = Curves()
    if not path.exists():
        return curves

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        curves.columns = list(reader.fieldnames or [])

        known = {c: _COLUMN_MAP[c] for c in curves.columns if c in _COLUMN_MAP}
        if "epoch" not in known:
            # 没有 epoch 列就不是可用的 results.csv
            return Curves(columns=curves.columns)

        buckets = {"losses": curves.losses, "metrics": curves.metrics, "lr": curves.lr}
        for col, (group, key) in known.items():
            if group in buckets:
                buckets[group][key] = []

        for row in reader:
            epoch = _to_float(row.get("epoch"))
            if epoch is None:
                continue
            curves.epochs.append(int(epoch))
            for col, (group, key) in known.items():
                if group in buckets:
                    buckets[group][key].append(_to_float(row.get(col)))

    return curves
