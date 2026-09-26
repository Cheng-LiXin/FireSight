"""解析 ultralytics 的 results.csv，产出可直接绘图的序列。

纯函数，便于单测。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# 序列里允许 None：某一轮某列缺失时留断点，前端据此断开折线而不是画到 0。
Series = List[Optional[float]]

# ultralytics 的指标键 → (分组, 前端用的键)。键名里的 "(B)" 后缀是它的标注、
# 随版本可能变，故查表前先剥掉。results.csv 的列名与之同构，两条路径共用这份映射。
_KEY_MAP: Dict[str, tuple] = {
    "epoch": ("axis", "epoch"),
    "train/box_loss": ("losses", "train_box"),
    "train/cls_loss": ("losses", "train_cls"),
    "train/dfl_loss": ("losses", "train_dfl"),
    "val/box_loss": ("losses", "val_box"),
    "val/cls_loss": ("losses", "val_cls"),
    "val/dfl_loss": ("losses", "val_dfl"),
    "metrics/precision": ("metrics", "precision"),
    "metrics/recall": ("metrics", "recall"),
    "metrics/mAP50": ("metrics", "mAP50"),
    "metrics/mAP50-95": ("metrics", "mAP50_95"),
    "lr/pg0": ("lr", "pg0"),
    "lr/pg1": ("lr", "pg1"),
    "lr/pg2": ("lr", "pg2"),
}

# 训练损失不出现在 trainer.metrics 里，只有 loss_items 有。
# 实测 ultralytics 8.4 返回的是 **dict**（{'box_loss': tensor, ...}），
# 不是按顺序的序列——按序列去迭代 dict 拿到的是键名，float() 会抛异常，
# 一旦被吞掉就是训练损失静默消失。两种形态都认。
LOSS_ITEM_ORDER = ("train_box", "train_cls", "train_dfl")
LOSS_ITEM_KEYS = ("box_loss", "cls_loss", "dfl_loss")


def map_key(raw: str) -> Optional[Tuple[str, str]]:
    """ultralytics 的列名或指标键 → (分组, 前端键)。不认识则返回 None。

    未知键一律跳过而非报错：ultralytics 换版本可能加列，不该因此整份解析失败。
    """
    return _KEY_MAP.get(str(raw).replace("(B)", "").strip())


def extract_epoch_metrics(
    metrics: Mapping[str, Any],
    loss_items: Any = None,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """把某一轮的 trainer.metrics 与 loss_items 收成 (losses, metrics)。

    键与 read_results_csv 的输出完全一致——实时曲线因此可以直接复用历史曲线的
    图表配置，不必维护两套。trainer.metrics 是 ultralytics 的内部结构，取不到
    就少几个键，不抛异常。
    """
    out_losses: Dict[str, float] = {}
    out_metrics: Dict[str, float] = {}

    for raw_key, value in (metrics or {}).items():
        mapped = map_key(raw_key)
        if not mapped:
            continue
        group, name = mapped
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if group == "losses":
            out_losses[name] = num
        elif group == "metrics":
            out_metrics[name] = num

    if isinstance(loss_items, Mapping):
        for name, key in zip(LOSS_ITEM_ORDER, LOSS_ITEM_KEYS):
            try:
                out_losses[name] = float(loss_items[key])
            except (KeyError, TypeError, ValueError):
                continue
    elif loss_items is not None:
        # 老版本按顺序给序列；顺序固定，多出来的项宁可丢掉也不猜
        try:
            values = [float(x) for x in loss_items]
        except (TypeError, ValueError):
            values = []
        for name, value in zip(LOSS_ITEM_ORDER, values):
            out_losses[name] = value

    return out_losses, out_metrics


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

        known: Dict[str, tuple] = {}
        for col in curves.columns:
            mapped = map_key(col)
            if mapped:
                known[col] = mapped
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
