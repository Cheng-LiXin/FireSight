"""check: 图-标签配对完整性。"""

from __future__ import annotations

from fire_sight_platform.data_validation.registry import CheckContext, CheckResult, CheckSeverity, check


@check("pair_existence")
def validate_pair_existence(ctx: CheckContext) -> CheckResult:
    missing = []
    total = 0
    for split, images in ctx.splits.items():
        label_dir = ctx.label_dirs.get(split)
        if label_dir is None or not label_dir.is_dir():
            if not images:
                # 这一划分本来就没有样本，「标签目录不存在」是 split_presence 的活
                continue
            # 标签目录整个不在，而不是「每张图都缺」——两件事要给两句话，否则
            # 排查的人会去逐个补标签，而真正的问题是布局不对。
            return CheckResult(
                "pair_existence",
                CheckSeverity.ERROR,
                f"{split} 的标签目录不存在（期望 {label_dir}）",
                ["检查 yaml 里的路径是否指向 images/labels 平级的提供方布局"],
            )
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
