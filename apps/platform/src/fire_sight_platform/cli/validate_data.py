"""fs-validate: 数据质检 CLI(只读)。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.paths import LOGGING_DIR, PROCESSED_DATA_DIR
from fire_sight_platform.data_validation.report import render_html_report, write_json_report
from fire_sight_platform.data_validation.registry import CheckSeverity
from fire_sight_platform.data_validation.service import validate_dataset

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs-validate", description="只读质检并产出 HTML/JSON 报告")
    p.add_argument("--dataset", required=True, help="processed/<dataset> 或 yaml 路径")
    p.add_argument("--out-dir", default=None, help="报告输出目录")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    get_logger(base_path=LOGGING_DIR, log_type="validate")

    ds = Path(args.dataset)
    if ds.suffix.lower() in {".yaml", ".yml"}:
        yaml_path = ds
        dataset_name = ds.parent.name
    else:
        dataset_name = ds.name
        yaml_path = PROCESSED_DATA_DIR / dataset_name / "data.yaml"
    if not yaml_path.exists():
        logger.error(f"找不到数据集 yaml: {yaml_path}")
        return 1

    results = validate_dataset(yaml_path)

    out_dir = Path(args.out_dir) if args.out_dir else yaml_path.parent / "reports"
    n_images = 0
    # rough counts from results not needed; read snapshot quickly
    from fire_sight_platform.data_validation.snapshot import build_snapshot

    ctx = build_snapshot(yaml_path)
    n_images = sum(len(v) for v in ctx.splits.values())
    n_labels = sum(1 for files in ctx.label_files.values() for _ in files)

    html_path = render_html_report(
        results,
        dataset=dataset_name,
        n_images=n_images,
        n_labels=n_labels,
        out_path=out_dir / "quality_report.html",
    )
    json_path = write_json_report(results, out_dir / "quality_report.json")

    errors = sum(1 for r in results if r.severity == CheckSeverity.ERROR)
    warns = sum(1 for r in results if r.severity == CheckSeverity.WARNING)
    logger.info(f"报告: {html_path}")
    logger.info(f"JSON: {json_path}")
    logger.info(f"汇总: ERROR={errors} WARNING={warns} CHECKS={len(results)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())


def _entry() -> None:
    raise SystemExit(main())


