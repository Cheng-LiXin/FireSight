"""fs-validate: 数据质检 CLI(只读)。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.naming import sanitize
from fire_sight_platform.common.paths import (
    LOGGING_DIR,
    PROCESSED_DATA_DIR,
    RAW_DATA_DIR,
    RUNTIME_DIR,
)
from fire_sight_platform.data_validation.report import render_html_report, write_json_report
from fire_sight_platform.data_validation.registry import CheckSeverity
from fire_sight_platform.data_validation.service import validate_dataset

logger = logging.getLogger(__name__)


def _resolve_yaml(dataset: str) -> Path:
    """把 --dataset 收成 data.yaml 的路径。

    与 `training.service._resolve_dataset_yaml` / `evaluation.service._resolve_yaml`
    同口径：除 yaml 路径与 processed 产物外，也认 `data/raw/D-Fire/data.yaml`
    （标签 `D-Fire/raw`）。否则体检新数据集只能去拼一个绝对路径。
    """
    raw = dataset.strip()
    ds = Path(raw)
    if ds.suffix.lower() in {".yaml", ".yml"}:
        return ds
    if raw == "D-Fire/raw":
        return RAW_DATA_DIR / "D-Fire" / "data.yaml"
    # 保留完整相对路径：支持 "D-Fire/train" 这类多级数据集名
    rel = raw.replace("\\", "/").strip("/")
    return PROCESSED_DATA_DIR / Path(rel) / "data.yaml"


def _report_dir(yaml_path: Path, dataset: str, out_dir: str | None) -> Path:
    """报告落在哪。

    产物数据集的报告跟产物放一起（`processed/<ds>/reports/`），而 raw 那份是
    提供方给的只读数据——体检报告是本项目的派生物，不能写进别人的目录，
    改落运行时目录（`.runtime/quality/`，已 gitignore）。
    """
    if out_dir:
        return Path(out_dir)
    try:
        yaml_path.resolve().relative_to(RAW_DATA_DIR.resolve())
    except (ValueError, OSError):
        return yaml_path.parent / "reports"
    return RUNTIME_DIR / "quality" / sanitize(dataset or "dataset")


def _dataset_label(dataset: str, yaml_path: Path) -> str:
    """报告标题上的数据集名。

    优先用 yaml 自报的 `fire_sight_meta.dataset`（pipeline 与提供方那份都写了），
    退回调用时给的标签，最后退回目录名。读不动 meta 不该让体检失败。
    """
    try:
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        meta_dataset = (payload.get("fire_sight_meta") or {}).get("dataset")
        if meta_dataset:
            return str(meta_dataset)
    except Exception:  # noqa: BLE001
        pass
    label = dataset.strip()
    if label.lower().endswith((".yaml", ".yml")):
        return yaml_path.parent.name or "dataset"
    return label or "dataset"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs-validate", description="只读质检并产出 HTML/JSON 报告")
    p.add_argument("--dataset", required=True, help="processed/<dataset>、D-Fire/raw 或 yaml 路径")
    p.add_argument("--out-dir", default=None, help="报告输出目录")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    get_logger(base_path=LOGGING_DIR, log_type="validate")

    yaml_path = _resolve_yaml(args.dataset)
    if not yaml_path.exists():
        logger.error(f"找不到数据集 yaml: {yaml_path}")
        return 1
    dataset_name = _dataset_label(args.dataset, yaml_path)

    results = validate_dataset(yaml_path)

    out_dir = _report_dir(yaml_path, args.dataset, args.out_dir)
    # 报告抬头要的两个计数。快照可能构建失败（yaml 写坏 / 路径类型不对），
    # 那时 run_all_checks 已经把它降级成一条 ERROR 结果——报告照出，计数填 0，
    # 而不是让整条 CLI 在这里再抛一次 traceback（2026-10-09 代码审查 R6）。
    from fire_sight_platform.data_validation.snapshot import build_snapshot

    try:
        ctx = build_snapshot(yaml_path)
        n_images = sum(len(v) for v in ctx.splits.values())
        n_labels = sum(1 for files in ctx.label_files.values() for _ in files)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"快照构建失败，报告计数按 0 出: {exc}")
        n_images = 0
        n_labels = 0

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


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    sys.exit(main())


