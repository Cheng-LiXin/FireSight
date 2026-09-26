"""模型评估:指标解析 + 多权重对比。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fire_sight_platform.common.paths import PROCESSED_DATA_DIR
from fire_sight_platform.runtime_config.base import ValConfig

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    weights: str
    dataset: str
    metrics: Dict[str, float] = field(default_factory=dict)
    per_class: Dict[str, float] = field(default_factory=dict)
    speed_ms: Dict[str, float] = field(default_factory=dict)


def _resolve_yaml(dataset: str) -> Path:
    p = Path(dataset)
    if p.suffix.lower() in {".yaml", ".yml"} and p.exists():
        return p
    yaml_path = PROCESSED_DATA_DIR / dataset / "data.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"找不到数据集 yaml: {yaml_path}")
    return yaml_path


def _pick(d: dict, *keys: str) -> Optional[float]:
    for k in keys:
        for dk, dv in d.items():
            if k.lower() in str(dk).lower() and isinstance(dv, (int, float)):
                return float(dv)
    return None


def evaluate_weights(cfg: ValConfig) -> EvalResult:
    """对单个权重跑验证集评估。"""
    from ultralytics import YOLO

    yaml_path = _resolve_yaml(cfg.dataset)
    if not cfg.weights:
        raise ValueError("缺少 --weights")
    model = YOLO(cfg.weights)
    results = model.val(
        data=str(yaml_path),
        imgsz=cfg.imgsz,
        batch=cfg.batch,
        conf=cfg.conf,
        iou=cfg.iou,
        device=cfg.device,
        verbose=True,
    )

    rd = {}
    try:
        rd = dict(results.results_dict or {})
    except Exception:
        pass

    speed = {}
    try:
        sp = getattr(results, "speed", {}) or {}
        speed = {str(k): float(v) for k, v in sp.items()}
    except Exception:
        pass

    per_class = {}
    try:
        names = getattr(results, "names", {}) or {}
        maps = getattr(results, "maps", None)
        if maps is not None:
            for i, v in enumerate(maps):
                per_class[str(names.get(i, i))] = float(v)
    except Exception:
        pass

    metrics = {
        "mAP50": _pick(rd, "map50", "mAP50") or 0.0,
        "mAP50_95": _pick(rd, "map50-95", "mAP50-95") or 0.0,
        "precision": _pick(rd, "precision") or 0.0,
        "recall": _pick(rd, "recall") or 0.0,
    }

    out = EvalResult(
        weights=str(cfg.weights),
        dataset=str(yaml_path),
        metrics=metrics,
        per_class=per_class,
        speed_ms=speed,
    )
    logger.info("评估结果".center(60, "="))
    logger.info(f"  weights   : {out.weights}")
    logger.info(f"  mAP@50    : {metrics['mAP50']:.4f}")
    logger.info(f"  mAP@50-95 : {metrics['mAP50_95']:.4f}")
    logger.info(f"  precision : {metrics['precision']:.4f}")
    logger.info(f"  recall    : {metrics['recall']:.4f}")
    if per_class:
        logger.info(f"  per-class : {per_class}")
    if speed:
        logger.info(f"  speed(ms) : {speed}")
    return out


def compare_runs(weight_list: List[str], dataset: str, **val_kwargs) -> List[EvalResult]:
    """多权重对比(如 yolo11n vs yolo11s)。"""
    results = []
    for w in weight_list:
        cfg = ValConfig(weights=w, dataset=dataset, **val_kwargs)
        results.append(evaluate_weights(cfg))
    logger.info("对照结果".center(60, "="))
    logger.info(f"{'weights':<32} {'mAP50':>8} {'mAP50-95':>10} {'P':>8} {'R':>8}")
    for r in results:
        logger.info(
            f"{Path(r.weights).name:<32} "
            f"{r.metrics['mAP50']:>8.4f} {r.metrics['mAP50_95']:>10.4f} "
            f"{r.metrics['precision']:>8.4f} {r.metrics['recall']:>8.4f}"
        )
    return results
