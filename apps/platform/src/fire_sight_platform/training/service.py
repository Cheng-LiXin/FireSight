"""训练编排器:顺序、验数、记录、归档。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

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
def _epoch_callback(emit: Callable[[Dict[str, Any]], None]):
    """把 ultralytics 的 trainer 对象收成与 curves API 同构的字典再回调。

    整体包一层 try：回调里抛异常会打断训练，进度上报失败不该毁掉一次几十分钟的任务。
    """
    from fire_sight_platform.training.curves import extract_epoch_metrics

    def handler(trainer: Any) -> None:
        try:
            epoch = int(getattr(trainer, "epoch", 0)) + 1
            epochs = int(getattr(trainer, "epochs", 0))
            if epochs and epoch > epochs:
                # 训练结束后的收尾验证也会触发本回调，它不是一轮训练。
                # 放行会在实时曲线上多出一个 epoch+1 的假点。
                return

            losses, metrics = extract_epoch_metrics(
                getattr(trainer, "metrics", {}) or {},
                getattr(trainer, "loss_items", None),
            )
            emit({"epoch": epoch, "epochs": epochs, "metrics": metrics, "losses": losses})
        except Exception:  # noqa: BLE001
            logger.debug("逐轮进度回调失败", exc_info=True)

    return handler


def train_yolo(
    cfg: TrainConfig,
    on_epoch: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> TrainResult:
    """按 TrainConfig 启动一次可归档的 YOLO 训练。

    on_epoch 每轮结束收到一次 {epoch, epochs, metrics, losses}。网页端据此推实时
    曲线；命令行不传即可，行为与从前一致。
    """
    from fire_sight_platform.common.runtime_env import (
        apply_runtime_env,
        pin_ultralytics_paths,
    )

    # 改道必须在 import ultralytics 之前——它在 import 时就把 settings.json 的
    # runs_dir / weights_dir / datasets_dir 读成模块常量了,晚一步就改不动。
    apply_runtime_env()

    from ultralytics import YOLO

    pin_ultralytics_paths()

    yaml_path = _resolve_dataset_yaml(cfg.dataset)
    log_device_info()
    logger.info(f"数据集: {yaml_path}")
    logger.info(
        f"模型={cfg.model} epochs={cfg.epochs} imgsz={cfg.imgsz} "
        f"batch={cfg.batch} lr0={cfg.lr0} device={cfg.device} "
        f"workers={cfg.workers} fraction={cfg.fraction} cache={cfg.cache}"
    )

    model = YOLO(cfg.model)
    if on_epoch is not None:
        # 用 on_fit_epoch_end 而非 on_train_epoch_end：后者触发时本轮验证还没跑，
        # 指标会慢一轮（第一轮全是 0）。ultralytics 也是在这一刻写 results.csv，
        # 两者因此严格对齐。
        model.add_callback("on_fit_epoch_end", _epoch_callback(on_epoch))

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
