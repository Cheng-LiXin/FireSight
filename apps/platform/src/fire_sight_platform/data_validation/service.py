"""质检服务层:跑全部 check 并聚合。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from fire_sight_platform.data_validation.registry import CheckResult, get_all_checks
from fire_sight_platform.data_validation.snapshot import build_snapshot

logger = logging.getLogger(__name__)


def run_all_checks(yaml_path: Path) -> List[CheckResult]:
    """跑全部 check。

    `build_snapshot` 也在守卫里：yaml 写坏、路径类型不对（比如 `train:` 是列表
    而某天又多了一种写法）都不该让整轮体检带 traceback 死掉。此前它在 try 之外，
    一个 `TypeError` 就能让 `fs-validate` 连报告都产不出来（2026-10-09 代码审查 R6）。
    """
    try:
        ctx = build_snapshot(yaml_path)
    except Exception as exc:  # noqa: BLE001
        from fire_sight_platform.data_validation.registry import CheckSeverity

        logger.error("数据快照构建失败: %s", exc)
        return [
            CheckResult(
                "snapshot",
                CheckSeverity.ERROR,
                f"无法解析数据集 yaml（{yaml_path}）: {exc}",
                ["检查 yaml 里的 path/train/val/test 是否为字符串或字符串列表"],
            )
        ]
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
