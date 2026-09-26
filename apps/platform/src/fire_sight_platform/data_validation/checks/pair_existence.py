"""check: 图-标签配对完整性。"""

from __future__ import annotations

from fire_sight_platform.data_validation.registry import CheckContext, CheckResult, CheckSeverity, check


@check("pair_existence")
def validate_pair_existence(ctx: CheckContext) -> CheckResult:
    missing = []
    total = 0
    for split, images in ctx.splits.items():
        label_dir = ctx.data_root / "labels" / split
        for img in images:
            total += 1
            if not (label_dir / f"{img.stem}.txt").exists():
                missing.append(f"{split}/{img.name}")
    if total == 0:
        return CheckResult("pair_existence", CheckSeverity.ERROR, "没有任何图像样本", [])
    ratio = len(missing) / total
    if ratio >= 0.5:
        sev = CheckSeverity.ERROR
    elif ratio >= 0.05:
        sev = CheckSeverity.WARNING
    elif missing:
        sev = CheckSeverity.INFO
    else:
        sev = CheckSeverity.PASS
    msg = f"缺失标签 {len(missing)}/{total} ({ratio:.1%})"
    return CheckResult("pair_existence", sev, msg, missing[:20])
