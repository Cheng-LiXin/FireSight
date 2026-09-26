"""check: 样本是否只属于一个 split(防泄漏)。"""

from __future__ import annotations

from collections import defaultdict

from fire_sight_platform.data_validation.registry import CheckContext, CheckResult, CheckSeverity, check


@check("split_uniqueness")
def validate_split_uniqueness(ctx: CheckContext) -> CheckResult:
    owner = defaultdict(list)
    for split, images in ctx.splits.items():
        for img in images:
            owner[img.stem].append(split)
    leaked = [f"{stem}: {splits}" for stem, splits in owner.items() if len(set(splits)) > 1]
    if leaked:
        return CheckResult(
            "split_uniqueness",
            CheckSeverity.ERROR,
            f"发现 {len(leaked)} 个样本跨 split 泄漏",
            leaked[:20],
        )
    return CheckResult("split_uniqueness", CheckSeverity.PASS, "无跨 split 泄漏")
