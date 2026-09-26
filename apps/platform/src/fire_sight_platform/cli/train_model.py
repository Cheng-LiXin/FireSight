"""fs-train: 训练 CLI。"""

from __future__ import annotations

import argparse
import logging
import sys

from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.paths import CONFIGS_DIR, LOGGING_DIR
from fire_sight_platform.runtime_config.base import TrainConfig
from fire_sight_platform.runtime_config.loaders import YAMLLoader, CLILoader
from fire_sight_platform.runtime_config.merger import ConfigMerger
from fire_sight_platform.training.service import train_yolo

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs-train", description="YOLO 训练(可归档)")
    p.add_argument("--dataset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--imgsz", type=int, default=None)
    p.add_argument("--batch", type=int, default=None)
    p.add_argument("--lr0", type=float, default=None)
    p.add_argument("--patience", type=int, default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--fraction", type=float, default=None, help="训练样本比例 0-1, 实验加速")
    p.add_argument("--cache", action="store_true", default=None, help="图像缓存进内存")
    p.add_argument("--device", default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--config", default=None, help="train.yaml 路径")
    p.add_argument("--experiment", default=None)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    get_logger(base_path=LOGGING_DIR, log_type="train")

    yaml_path = args.config or (CONFIGS_DIR / "runtime" / "train.yaml")
    yaml_data = YAMLLoader().load(yaml_path) if yaml_path else {}
    cli_data = CLILoader().load(
        {
            "dataset": args.dataset,
            "model": args.model,
            "epochs": args.epochs,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "lr0": args.lr0,
            "patience": args.patience,
            "workers": args.workers,
            "fraction": args.fraction,
            "cache": args.cache,
            "device": args.device,
            "seed": args.seed,
            "experiment": args.experiment,
        }
    )
    cfg, sources = ConfigMerger(TrainConfig).merge(yaml_data, cli_data)
    logger.info(f"配置来源: {sources}")
    try:
        result = train_yolo(cfg)
    except Exception as exc:
        logger.error(f"训练失败: {exc}", exc_info=True)
        return 1
    logger.info(f"训练完成 run_id={result.run_id} best={result.best_pt}")
    return 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
