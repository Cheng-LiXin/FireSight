"""类别平衡报告:转换后、划分前。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence


@dataclass(frozen=True)
class ClassStat:
    class_id: int
    name: str
    count: int


@dataclass(frozen=True)
class BalanceReport:
    stats: List[ClassStat]
    total_boxes: int

    def format_table(self) -> str:
        if not self.stats:
            return "(无标注框)"
        lines = ["类别分布:", f"{'ID':>4}  {'名称':<16}  {'数量':>8}"]
        for s in self.stats:
            lines.append(f"{s.class_id:>4}  {s.name:<16}  {s.count:>8}")
        lines.append(f"合计框数: {self.total_boxes}")
        return "\n".join(lines)

    def rare_classes(self, min_expected: float = 3.0) -> List[str]:
        return [s.name for s in self.stats if s.count < min_expected]


def analyze_balance(label_dir: Path, names: Sequence[str]) -> BalanceReport:
    counts: Counter[int] = Counter()
    for txt in Path(label_dir).glob("*.txt"):
        for line in txt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            cid = int(float(line.split()[0]))
            counts[cid] += 1
    stats = [
        ClassStat(
            class_id=i,
            name=names[i] if i < len(names) else f"class_{i}",
            count=counts.get(i, 0),
        )
        for i in range(len(names))
    ]
    return BalanceReport(stats=stats, total_boxes=sum(counts.values()))
