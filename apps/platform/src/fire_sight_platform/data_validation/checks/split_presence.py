"""check: train/val/test 划分是否可用。"""

from __future__ import annotations

from fire_sight_platform.data_validation.registry import CheckContext, CheckResult, CheckSeverity, check


@check("split_presence")
def validate_split_presence(ctx: CheckContext) -> CheckResult:
    empty = [s for s, imgs in ctx.splits.items() if len(imgs) == 0]
    detail = [f"{s}: {len(ctx.splits[s])} images" for s in ("train", "val", "test")]
    if "val" in empty:
        return CheckResult("split_presence", CheckSeverity.ERROR, "val 划分为空", detail)
    if empty:
        return CheckResult(
            "split_presence", CheckSeverity.WARNING, f"空划分: {empty}", detail
        )
    return CheckResult("split_presence", CheckSeverity.PASS, "各划分均有样本", detail)
