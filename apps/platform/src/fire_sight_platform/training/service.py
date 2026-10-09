"""训练编排器:顺序、验数、记录、归档。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fire_sight_platform.common.paths import PROCESSED_DATA_DIR, RAW_DATA_DIR, RUNS_DIR
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
    """把数据集标签解析成 data.yaml 的路径。

    两个来源，与 `infer._dataset_dirs()` 的口径保持一致：

    - `data/processed/<标签>/data.yaml` —— pipeline 产物，可重建
    - `data/raw/D-Fire/data.yaml`（标签 `D-Fire/raw`）—— 提供方切好的，不可重建

    raw 那一份刻意不放进 processed，理由写在那个 data.yaml 的注释里：切分不是
    本项目做的，放 processed 会暗示它可由 pipeline 重生成。所以这里要显式认它，
    否则界面上能选、一训练就报「找不到数据集 yaml」。
    """
    p = Path(dataset)
    if p.suffix.lower() in {".yaml", ".yml"} and p.exists():
        return p
    if dataset == "D-Fire/raw":
        raw_yaml = RAW_DATA_DIR / "D-Fire" / "data.yaml"
        if raw_yaml.exists():
            return raw_yaml
        raise FileNotFoundError(f"找不到数据集 yaml: {raw_yaml}")
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


def read_checkpoint(path: Path | str) -> Dict[str, Any]:
    """读训练检查点的元信息，用于判断能不能断点续训。

    抽成公开函数是因为**路由也要用**：它要据此决定放行还是给出可读的拒绝理由，
    而真正加载模型是在训练子进程里做的事。

    判据是 `optimizer` 在不在——`Trainer.final_eval()` 会调 `strip_optimizer(last)`，
    所以**跑完的现场，last.pt 只剩权重**，从它「续训」等于从那一轮的权重微调，
    学习率轨迹接不上。这个区别必须让调用方看得见。
    """
    import torch

    ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
    args = ckpt.get("train_args") or {}
    # epoch 是 0-based：epoch=39 表示已完成 40 轮
    done = int(ckpt.get("epoch") or 0) + 1
    return {
        "done_epochs": done,
        "total_epochs": int(args.get("epochs") or 0),
        "imgsz": int(args.get("imgsz") or 0),
        "batch": int(args.get("batch") or 0),
        "device": str(args.get("device") or ""),
        # 守卫要按**检查点的模型**算规模因子；用请求里的默认值会拿 yolo11n 的额度
        # 放行 yolo11s（实测差 1.82 倍）
        "model": str(args.get("model") or ""),
        "data": str(args.get("data") or ""),
        "project": str(args.get("project") or ""),
        "name": str(args.get("name") or ""),
        "has_optimizer": ckpt.get("optimizer") is not None,
        "has_ema": ckpt.get("ema") is not None,
        "updates": int(ckpt.get("updates") or 0),
    }


def _train_from_checkpoint(
    cfg: TrainConfig,
    on_epoch: Optional[Callable[[Dict[str, Any]], None]],
    on_run: Optional[Callable[[str], None]] = None,
):
    """从 `cfg.resume`（现场目录的 last.pt）续训，返回 (results, run_id, data_yaml)。

    四条 ultralytics 的**实际语义**，读 `Trainer.check_resume` 得来，别照直觉改：

    1. 参数以 **checkpoint 里的 `train_args`** 为准；cfg 里那些基本不生效。
       只有白名单里的键（`imgsz/batch/device/workers/cache/patience/val/plots/
       save_dir/…`）**且显式传入**时才会覆盖 checkpoint 的值。
    2. `imgsz` **必须显式传**。加载权重时 `self.overrides` 里带着该权重的 imgsz
       （`_reset_ckpt_args` 的 include 集合含 `imgsz`），而它在白名单里——
       不传就会被加载权重的值盖掉 checkpoint 的值。
    3. 现场目录取 checkpoint 的 `project/name`，**不另造**。这里不传 project/name：
       它们不在白名单里，传了也会被丢弃，`save_dir` 仍来自 checkpoint。
    4. `epochs` 不在白名单里，取 checkpoint 的值——**续训改不了总轮数**。
    """
    info = read_checkpoint(cfg.resume)
    if not info["has_optimizer"]:
        raise ValueError(
            f"该现场的训练已经结束（停在第 {info['done_epochs']} 轮），"
            "检查点里的优化器状态已被 strip_optimizer 剥离，无法断点续训。"
            "要接着练，请用常规训练从对应的 best.pt 微调。"
        )
    if info["total_epochs"] and info["done_epochs"] >= info["total_epochs"]:
        raise ValueError(
            f"该现场已跑满 {info['total_epochs']} 轮，没有可续的轮次。"
        )

    run_id = info["name"] or Path(cfg.resume).parent.parent.name
    imgsz = info["imgsz"] or cfg.imgsz
    _notify_run(on_run, run_id)

    logger.info("断点续训".center(60, "="))
    logger.info(f"  检查点  : {cfg.resume}")
    logger.info(f"  已完成  : {info['done_epochs']}/{info['total_epochs']} 轮")
    logger.info(f"  现场    : {run_id}")
    logger.info(
        f"  imgsz={imgsz} batch={info['batch']} device={cfg.device}"
        f"（imgsz/batch/epochs 以检查点为准，device 允许改）"
    )

    from ultralytics import YOLO

    model = YOLO(cfg.resume)
    if on_epoch is not None:
        model.add_callback("on_fit_epoch_end", _epoch_callback(on_epoch))

    results = model.train(
        resume=str(cfg.resume),
        imgsz=imgsz,  # 见文档第 2 条：不显式传就会用加载权重的 imgsz
        device=cfg.device,  # 白名单项，允许换设备（例如改成 cpu）
        verbose=True,
    )
    return results, run_id, Path(info["data"]) if info["data"] else None


def _notify_run(on_run: Optional[Callable[[str], None]], run_id: str) -> None:
    """把 run_id 报给上层。**必须早于 model.train()**。

    上层（网页端）要在「任务还没跑完」时就知道这次训练落到哪个现场。等到训练
    结束才说，被暂停的任务永远等不到那一刻——用户点了暂停，界面就再也说不出
    该续哪个现场。回调里抛异常不该毁掉一次几十分钟的训练，所以整条吞掉。
    """
    if on_run is None:
        return
    try:
        on_run(run_id)
    except Exception:  # noqa: BLE001
        logger.exception("上报 run_id 失败（不中断训练）")


def train_yolo(
    cfg: TrainConfig,
    on_epoch: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_run: Optional[Callable[[str], None]] = None,
) -> TrainResult:
    """按 TrainConfig 启动一次可归档的 YOLO 训练。

    on_epoch 每轮结束收到一次 {epoch, epochs, metrics, losses}。网页端据此推实时
    曲线；命令行不传即可，行为与从前一致。

    on_run 在**训练真正开跑之前**收到一次 run_id（现场目录名）。网页端靠它把
    「正在跑的任务」和 runs/ 下那个目录对上——训练被中途暂停时没有 done 事件，
    只能在这里提前知道。

    `cfg.resume` 非空时走断点续训：见 `_train_from_checkpoint`。
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

    log_device_info()

    if cfg.resume:
        results, run_id, yaml_path = _train_from_checkpoint(cfg, on_epoch, on_run)
    else:
        results, run_id, yaml_path = _train_fresh(cfg, on_epoch, on_run, YOLO)
        logger.info(f"数据集: {yaml_path}")

    return _finish(results, cfg, run_id, yaml_path)


def _train_fresh(
    cfg: TrainConfig,
    on_epoch: Optional[Callable[[Dict[str, Any]], None]],
    on_run: Optional[Callable[[str], None]],
    YOLO,
):
    """常规训练：从预训练权重起一次新的现场。"""
    yaml_path = _resolve_dataset_yaml(cfg.dataset)
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
    # 上报的是**现场目录名**（runs/training 下的那一层），不是归档名。两者只在
    # 没设 experiment 时相同；resume_run_id 认的是前者，界面必须拿到对的那个。
    _notify_run(on_run, exp)
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
    return results, run_id, yaml_path


def _finish(results, cfg: TrainConfig, run_id: str, yaml_path) -> TrainResult:
    """两种训练路径共用的收尾：找权重、抽指标、归档。"""
    project = RUNS_DIR / "training"
    save_dir = Path(getattr(results, "save_dir", project / run_id))
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
