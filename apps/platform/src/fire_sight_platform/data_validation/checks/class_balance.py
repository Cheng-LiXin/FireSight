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
    # 分母要按**声明过的类别**取，而不是只在出现过的 class id 上取 min。
    # 后者会让「整个类别一个框都没有」悄无声息地变成 PASS：100 个框全是 class 0
    # 时失衡比算成 1.0（只有一个类别，自己比自己），而补进一个 class-1 框反倒
    # 立刻变成 WARNING 100.0。缺类比失衡严重得多，不能反而判得更轻
    # （2026-10-09 代码审查 R5）。
    # 未声明的 class id（标签里写了 names 没有的号）也纳入，label_format 会另报。
    classes = sorted(set(ctx.names) | set(counts))
    top = max(counts.values())
    min_v = min(counts.get(i, 0) for i in classes)
    ratio = top / min_v if min_v else float("inf")
    lines = [f"{ctx.names.get(k, k)}: {counts.get(k, 0)}" for k in classes]
    if min_v == 0:
        absent = [str(ctx.names.get(i, i)) for i in classes if counts.get(i, 0) == 0]
        return CheckResult(
            "class_balance",
            CheckSeverity.ERROR,
            f"{len(absent)} 个类别一个标注框都没有: {' / '.join(absent)}",
            lines,
        )
    if ratio >= 50:
        sev, msg = CheckSeverity.WARNING, f"类别失衡比 {ratio:.1f}"
    elif ratio >= 10:
        sev, msg = CheckSeverity.INFO, f"类别失衡比 {ratio:.1f}"
    else:
        sev, msg = CheckSeverity.PASS, f"类别分布可接受 (失衡比 {ratio:.1f})"
    return CheckResult("class_balance", sev, msg, lines)
