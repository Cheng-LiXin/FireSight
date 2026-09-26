"""训练编排器:顺序、验数、记录、归档。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fire_sight_platform.common.paths import PROCESSED_DATA_DIR, RUNS_DIR
from fire_sight_platform.common.performance_utils import time_it
from fire_sight_platform.common.system_utils import log_device_info
from fire_sight_platform.runtime_config.base import TrainConfig
from fire_sight_platform.training.archive import archive_best_weight, make_run_id
from fire_sight_platform.training.result import extract_metrics, log_train_metrics

logger = logging.getLogger(__name__)


@dataclass
class TrainResult:
    run_id: str
    best_pt: Path
    metrics: Any
    save_dir: Path
    dataset_yaml: Path


def _resolve_dataset_yaml(dataset: str) -> Path:
    p = Path(dataset)
    if p.suffix.lower() in {".yaml", ".yml"} and p.exists():
        return p
    yaml_path = PROCESSED_DATA_DIR / dataset / "data.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"找不到数据集 yaml: {yaml_path}(请先 fs-transform)")
    return yaml_path


@time_it(iterations=1, name="模型训练", logger_instance=logger)
def train_yolo(cfg: TrainConfig) -> TrainResult:
    """按 TrainConfig 启动一次可归档的 YOLO 训练。"""
    from ultralytics import YOLO

    yaml_path = _resolve_dataset_yaml(cfg.dataset)
    log_device_info()
    logger.info(f"数据集: {yaml_path}")
    logger.info(
        f"模型={cfg.model} epochs={cfg.epochs} imgsz={cfg.imgsz} "
        f"batch={cfg.batch} lr0={cfg.lr0} device={cfg.device} "
        f"workers={cfg.workers} fraction={cfg.fraction} cache={cfg.cache}"
    )

    model = YOLO(cfg.model)
    run_id = make_run_id(cfg.dataset, Path(cfg.model).stem)
    exp = (cfg.experiment or "").strip()
    if exp.lower() in {"", "none", "null"}:
        exp = run_id
    project = RUNS_DIR / "training"
    results = model.train(
        data=str(yaml_path),
        epochs=cfg.epochs,
        imgsz=cfg.imgsz,
        batch=cfg.batch,
        lr0=cfg.lr0,
        patience=cfg.patience,
        workers=cfg.workers,
        fraction=cfg.fraction,
        cache=cfg.cache,
        seed=cfg.seed,
        device=cfg.device,
        project=str(project),
        name=exp,
        exist_ok=True,
        verbose=True,
    )

    save_dir = Path(getattr(results, "save_dir", project / exp))
    best_pt = save_dir / "weights" / "best.pt"
    if not best_pt.exists():
        last_pt = save_dir / "weights" / "last.pt"
        if last_pt.exists():
            best_pt = last_pt
        else:
            raise FileNotFoundError(f"训练结束但未找到权重: {save_dir}/weights")

    metrics = extract_metrics(results, cfg.dataset, cfg.model, cfg.epochs)
    log_train_metrics(metrics)

    config_text = cfg.model_dump_json(indent=2)
    metrics_text = (
        f"dataset={cfg.dataset}\nmodel={cfg.model}\n"
        f"mAP50={metrics.metrics_mAP50}\nmAP50_95={metrics.metrics_mAP50_95}\n"
        f"precision={metrics.metrics_precision}\nrecall={metrics.metrics_recall}\n"
    )
    archived = archive_best_weight(
        best_pt, run_id=run_id, config_text=config_text, metrics_text=metrics_text
    )

    return TrainResult(
        run_id=run_id,
        best_pt=archived,
        metrics=metrics,
        save_dir=save_dir,
        dataset_yaml=yaml_path,
    )
