"""fs-val: 模型评估 CLI。"""

from __future__ import annotations

import argparse
import logging
import sys

from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.paths import LOGGING_DIR
from fire_sight_platform.evaluation.service import compare_runs, evaluate_weights
from fire_sight_platform.runtime_config.base import ValConfig

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs-val", description="模型评估 / 多权重对照")
    p.add_argument("--dataset", required=True)
    p.add_argument("--weights", nargs="+", required=True, help="一个或多个权重路径")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.6)
    p.add_argument("--device", default="0")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    get_logger(base_path=LOGGING_DIR, log_type="val")
    try:
        if len(args.weights) == 1:
            cfg = ValConfig(
                dataset=args.dataset,
                weights=args.weights[0],
                imgsz=args.imgsz,
                batch=args.batch,
                conf=args.conf,
                iou=args.iou,
                device=args.device,
            )
            evaluate_weights(cfg)
        else:
            compare_runs(
                args.weights,
                dataset=args.dataset,
                imgsz=args.imgsz,
                batch=args.batch,
                conf=args.conf,
                iou=args.iou,
                device=args.device,
            )
    except Exception as exc:
        logger.error(f"评估失败: {exc}", exc_info=True)
        return 1
    return 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
