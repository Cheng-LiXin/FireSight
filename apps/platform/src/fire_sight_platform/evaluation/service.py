"""模型评估:指标解析 + 多权重对比。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fire_sight_platform.common.paths import PROCESSED_DATA_DIR, RAW_DATA_DIR
from fire_sight_platform.runtime_config.base import ValConfig

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    weights: str
    dataset: str
    metrics: Dict[str, float] = field(default_factory=dict)
    per_class: Dict[str, float] = field(default_factory=dict)
    speed_ms: Dict[str, float] = field(default_factory=dict)


def _resolve_yaml(dataset: str, split: str = "val") -> Path:
    """把数据集标签解析成 data.yaml 的路径。

    两个来源，与 `training.service._resolve_dataset_yaml` 的口径一致：
    processed 下的产物，以及 raw 下提供方切好的那一份。

    `split` 只在 raw 那份上有用武之地——processed 的 data.yaml 里 train/val/test
    是同级键，ultralytics 的 `split=` 参数能直接选；raw 那份同理（三个键都在顶层）。
    这里返回的是同一份 yaml，选哪个划分交给 `model.val(split=...)`。
    """
    del split  # 仅供调用方记录用；选择划分在 model.val(split=) 上
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
        raise FileNotFoundError(f"找不到数据集 yaml: {yaml_path}")
    return yaml_path


def _pick(d: dict, *keys: str) -> Optional[float]:
    for k in keys:
        for dk, dv in d.items():
            if k.lower() in str(dk).lower() and isinstance(dv, (int, float)):
                return float(dv)
    return None


def evaluate_weights(cfg: ValConfig) -> EvalResult:
    """对单个权重跑指定划分的评估。

    默认 `val`；`test` 只在最终汇报时用。两者都是同一份 data.yaml 里的键，
    由 `split=` 选择——不要另建一份只指向 test 的 yaml，那会让「哪个划分」
    这件事有两个真相（yaml 一个、请求参数一个），迟早对不上。
    """
    from fire_sight_platform.common.paths import RUNTIME_EVAL_DIR
    from fire_sight_platform.common.runtime_env import (
        apply_runtime_env,
        pin_ultralytics_paths,
    )

    # 同 training/service.py:必须在 import ultralytics 之前改道。
    apply_runtime_env()

    from ultralytics import YOLO

    pin_ultralytics_paths()

    split = (cfg.split or "val").lower()
    yaml_path = _resolve_yaml(cfg.dataset, split)
    if not cfg.weights:
        raise ValueError("缺少 --weights")
    model = YOLO(cfg.weights)
    results = model.val(
        data=str(yaml_path),
        split=split,
        imgsz=cfg.imgsz,
        batch=cfg.batch,
        conf=cfg.conf,
        iou=cfg.iou,
        device=cfg.device,
        verbose=True,
        # 必须显式给 project/name:不给就落到 ultralytics 全局的 runs_dir 上,
        # 本机那份配置指向 C 盘(实测写出过 runs/detect/val、val-2)。
        # 评估图件是副产物、没有消费方,统一丢进运行时目录,别脏了 runs/。
        # name 带上 split:同一个模型先测 val 再测 test 时,不加就互相覆盖,
        # 留下的是后跑的那份而文件名叫 val(实测踩过:两个模型都叫 best)。
        project=str(RUNTIME_EVAL_DIR),
        name=f"{Path(cfg.weights).stem or 'val'}-{split}",
        exist_ok=True,
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
