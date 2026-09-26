"""check: YOLO 标签行格式与取值。"""

from __future__ import annotations

from fire_sight_platform.data_validation.labels import parse_label_line
from fire_sight_platform.data_validation.registry import CheckContext, CheckResult, CheckSeverity, check


@check("label_format")
def validate_label_format(ctx: CheckContext) -> CheckResult:
    errors = []
    lines = 0
    for split, files in ctx.label_files.items():
        for path in files:
            for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not raw.strip():
                    continue
                lines += 1
                parsed = parse_label_line(raw)
                if parsed is None:
                    errors.append(f"{split}/{path.name}:{i} 无法解析")
                    continue
                cid, cx, cy, w, h = parsed
                if cid not in ctx.names:
                    errors.append(f"{split}/{path.name}:{i} 未知 class_id={cid}")
                if not all(0.0 <= v <= 1.0 for v in (cx, cy, w, h)):
                    errors.append(f"{split}/{path.name}:{i} 坐标越界")
                if w <= 0 or h <= 0:
                    errors.append(f"{split}/{path.name}:{i} 宽高非正")
    if errors:
        return CheckResult(
            "label_format",
            CheckSeverity.ERROR,
            f"{len(errors)} 行标签非法 (共 {lines} 行)",
            errors[:20],
        )
    return CheckResult("label_format", CheckSeverity.PASS, f"标签格式正常 (共 {lines} 行)")
