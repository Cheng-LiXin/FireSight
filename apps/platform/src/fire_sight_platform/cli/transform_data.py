"""fs-transform: 数据流水线 CLI(转换 + 冻结划分)。"""

from __future__ import annotations

import argparse
import logging
import sys

from fire_sight_platform.common.constants import (
    DEFAULT_RANDOM_STATE,
    DEFAULT_SPLIT_STRATEGY,
    DEFAULT_TRAIN_RATE,
    DEFAULT_VAL_RATE,
    AnnotationFormat,
)
from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.paths import LOGGING_DIR
from fire_sight_platform.data_pipeline.orchestrator import run_transform

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs-transform", description="原始数据集 → 可训练 YOLO 成品")
    p.add_argument("--dataset", required=True, help="数据集名(位于 data/raw/<dataset>)")
    p.add_argument(
        "--format",
        default=AnnotationFormat.YOLO,
        choices=list(AnnotationFormat.all()),
        help="源标注格式",
    )
    p.add_argument("--classes", default="flame,smoke", help="类别名,逗号分隔")
    p.add_argument("--seed", type=int, default=DEFAULT_RANDOM_STATE)
    p.add_argument("--train-rate", type=float, default=DEFAULT_TRAIN_RATE)
    p.add_argument("--val-rate", type=float, default=DEFAULT_VAL_RATE)
    p.add_argument("--split", default=DEFAULT_SPLIT_STRATEGY, dest="split_strategy")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    get_logger(base_path=LOGGING_DIR, log_type="transform")
    class_names = [c.strip() for c in args.classes.split(",") if c.strip()]
    try:
        result = run_transform(
            dataset=args.dataset,
            source_format=args.format,
            class_names=class_names,
            seed=args.seed,
            train_rate=args.train_rate,
            val_rate=args.val_rate,
            split_strategy=args.split_strategy,
        )
    except Exception as exc:
        logger.error(f"流水线失败: {exc}", exc_info=True)
        return 1
    logger.info(f"完成: yaml={result.yaml_path} splits={result.split_counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def _entry() -> None:
    raise SystemExit(main())


