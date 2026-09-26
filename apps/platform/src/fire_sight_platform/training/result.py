"""训练结果指标(纯数据)。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainMetrics:
    best_fitness: Optional[float]
    metrics_mAP50: Optional[float]
    metrics_mAP50_95: Optional[float]
    metrics_precision: Optional[float]
    metrics_recall: Optional[float]
    epochs: int
    dataset: str
    model: str


def log_train_metrics(m: TrainMetrics) -> None:
    logger.info("训练指标".center(60, "="))
    logger.info(f"  dataset     : {m.dataset}")
    logger.info(f"  model       : {m.model}")
    logger.info(f"  epochs      : {m.epochs}")
    logger.info(f"  mAP@50      : {m.metrics_mAP50}")
    logger.info(f"  mAP@50-95   : {m.metrics_mAP50_95}")
    logger.info(f"  precision   : {m.metrics_precision}")
    logger.info(f"  recall      : {m.metrics_recall}")


def extract_metrics(results: Any, dataset: str, model: str, epochs: int) -> TrainMetrics:
    def _f(name: str) -> Optional[float]:
        try:
            v = getattr(results, "results_dict", {}) or {}
            # ultralytics keys like metrics/mAP50(B)
            for k, val in v.items():
                if name.lower() in k.lower():
                    return float(val)
        except Exception:
            return None
        return None

    return TrainMetrics(
        best_fitness=_f("fitness"),
        metrics_mAP50=_f("map50"),
        metrics_mAP50_95=_f("map50-95"),
        metrics_precision=_f("precision"),
        metrics_recall=_f("recall"),
        epochs=epochs,
        dataset=dataset,
        model=model,
    )
