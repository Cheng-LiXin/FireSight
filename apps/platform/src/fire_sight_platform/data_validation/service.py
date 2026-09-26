"""质检服务层:跑全部 check 并聚合。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from fire_sight_platform.data_validation.registry import CheckResult, get_all_checks
from fire_sight_platform.data_validation.snapshot import build_snapshot

logger = logging.getLogger(__name__)


def run_all_checks(yaml_path: Path) -> List[CheckResult]:
    ctx = build_snapshot(yaml_path)
    results: List[CheckResult] = []
    for entry in get_all_checks():
        try:
            results.append(entry.func(ctx))
        except Exception as exc:  # 单项失败不拖垮整体
            from fire_sight_platform.data_validation.registry import CheckSeverity

            results.append(
                CheckResult(entry.name, CheckSeverity.ERROR, f"check 崩溃: {exc}", [])
            )
    return results


def validate_dataset(yaml_path: Path) -> List[CheckResult]:
    results = run_all_checks(yaml_path)
    for r in results:
        level = {
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "INFO": logging.INFO,
            "PASS": logging.INFO,
        }[r.severity.value]
        logger.log(level, "[%s] %s — %s", r.severity.value, r.name, r.message)
    return results
