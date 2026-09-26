"""check: 类别失衡提醒。"""

from __future__ import annotations

from collections import Counter

from fire_sight_platform.data_validation.labels import parse_label_line
from fire_sight_platform.data_validation.registry import CheckContext, CheckResult, CheckSeverity, check


@check("class_balance")
def validate_class_balance(ctx: CheckContext) -> CheckResult:
    counts: Counter[int] = Counter()
    for files in ctx.label_files.values():
        for path in files:
            for raw in path.read_text(encoding="utf-8").splitlines():
                parsed = parse_label_line(raw)
                if parsed:
                    counts[parsed[0]] += 1
    if not counts:
        return CheckResult("class_balance", CheckSeverity.WARNING, "没有任何标注框")
    top = max(counts.values())
    min_v = min(counts.values())
    ratio = top / min_v if min_v else float("inf")
    lines = [f"{ctx.names.get(k, k)}: {v}" for k, v in sorted(counts.items())]
    if ratio >= 50:
        sev, msg = CheckSeverity.WARNING, f"类别失衡比 {ratio:.1f}"
    elif ratio >= 10:
        sev, msg = CheckSeverity.INFO, f"类别失衡比 {ratio:.1f}"
    else:
        sev, msg = CheckSeverity.PASS, f"类别分布可接受 (失衡比 {ratio:.1f})"
    return CheckResult("class_balance", sev, msg, lines)
