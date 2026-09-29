"""大屏聚合统计(只读，不改库)。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fire_sight.alerting import EventStore
from fire_sight_platform.common.paths import MODELS_DIR


class StatsService:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def daily_alerts(self, days: int = 30) -> List[Dict[str, Any]]:
        """按日聚合告警数与平均置信度。"""
        with EventStore(self.db_path) as store:
            rows = store.list_events(limit=100000)
        bucket: Dict[str, Dict[str, Any]] = {}
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        for r in rows:
            ts = (r.get("ts") or "")[:10]
            if not ts or ts < cutoff:
                continue
            b = bucket.setdefault(ts, {"date": ts, "count": 0, "conf_sum": 0.0})
            b["count"] += 1
            b["conf_sum"] += float(r.get("conf") or 0)
        out = []
        for date in sorted(bucket):
            b = bucket[date]
            out.append(
                {
                    "date": date,
                    "count": b["count"],
                    "avg_conf": round(b["conf_sum"] / b["count"], 4) if b["count"] else 0,
                }
            )
        return out

    def class_ratio(self) -> Dict[str, int]:
        with EventStore(self.db_path) as store:
            rows = store.list_events(limit=100000)
        ratio: Dict[str, int] = {"fire": 0, "smoke": 0}
        for r in rows:
            labels = (r.get("labels") or "").split(",")
            for lab in labels:
                lab = lab.strip()
                if lab in ratio:
                    ratio[lab] += 1
        return ratio

    # ------------------------------------------------------------------
    # 月度（大屏）
    # ------------------------------------------------------------------

    @staticmethod
    def _month_key(ts: Any) -> str:
        return (ts or "")[:7]

    @staticmethod
    def _month_range(months: int, end_month: str = "") -> List[str]:
        """连续 months 个月的键（YYYY-MM），升序，末位是 end_month（默认当月）。"""
        if end_month:
            end = datetime.strptime(end_month, "%Y-%m")
        else:
            end = datetime.now().replace(day=1)
        out: List[str] = []
        y, m = end.year, end.month
        for _ in range(max(1, int(months))):
            out.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return list(reversed(out))

    def _rows(self) -> List[Dict[str, Any]]:
        # 只读统计不该因为被访问就凭空把事件库文件建出来
        if not self.db_path.exists():
            return []
        with EventStore(self.db_path) as store:
            return store.list_events(limit=100000)

    @staticmethod
    def _count_labels(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        """按类别计数；fire,smoke 这类混合事件在两类各计一次——与 class_ratio 同口径。"""
        out = {"fire": 0, "smoke": 0}
        for r in rows:
            for lab in (r.get("labels") or "").split(","):
                lab = lab.strip()
                if lab in out:
                    out[lab] += 1
        return out

    @staticmethod
    def _is_fp(r: Dict[str, Any]) -> bool:
        return int(r.get("is_false_positive") or 0) == 1

    def monthly_alerts(self, months: int = 12, end_month: str = "") -> List[Dict[str, Any]]:
        """按月聚合；空月份补 0 而不是缺一根——时间序列必须连续。"""
        rows = self._rows()
        by_month: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            key = self._month_key(r.get("ts"))
            if key:
                by_month.setdefault(key, []).append(r)

        out = []
        for month in self._month_range(months, end_month):
            bucket = by_month.get(month, [])
            labels = self._count_labels(bucket)
            confs = [float(r.get("conf") or 0) for r in bucket]
            out.append(
                {
                    "month": month,
                    "count": len(bucket),
                    "fire": labels["fire"],
                    "smoke": labels["smoke"],
                    "false_positives": sum(1 for r in bucket if self._is_fp(r)),
                    "avg_conf": round(sum(confs) / len(confs), 4) if confs else 0.0,
                }
            )
        return out

    def month_detail(self, month: str) -> Dict[str, Any]:
        """单月明细：读数、24 小时时段分布、环比。

        环比在上月为 0 时给 None——「从 0 涨到 N」不是 100%，是无从比较。
        """
        month = (month or "")[:7]
        all_rows = self._rows()
        rows = [r for r in all_rows if self._month_key(r.get("ts")) == month]
        labels = self._count_labels(rows)
        fp = sum(1 for r in rows if self._is_fp(r))
        confs = [float(r.get("conf") or 0) for r in rows]

        hourly = [0] * 24
        for r in rows:
            try:
                hourly[int(str(r.get("ts"))[11:13])] += 1
            except (ValueError, IndexError):
                continue

        prev_month = self._month_range(2, month)[0]
        prev_rows = [r for r in all_rows if self._month_key(r.get("ts")) == prev_month]
        if prev_rows:
            mom_pct: Optional[float] = round(
                (len(rows) - len(prev_rows)) / len(prev_rows) * 100, 1
            )
        else:
            mom_pct = None

        return {
            "month": month,
            "total": len(rows),
            "fire": labels["fire"],
            "smoke": labels["smoke"],
            "false_positives": fp,
            "pending": len(rows) - fp,
            "false_positive_rate": round(fp / len(rows), 4) if rows else 0.0,
            "avg_conf": round(sum(confs) / len(confs), 4) if confs else 0.0,
            "hourly": hourly,
            "mom_pct": mom_pct,
        }

    def false_positive_rate(self) -> float:
        with EventStore(self.db_path) as store:
            rows = store.list_events(limit=100000)
        if not rows:
            return 0.0
        fp = sum(1 for r in rows if int(r.get("is_false_positive") or 0) == 1)
        return round(fp / len(rows), 4)

    def model_metrics(self) -> Dict[str, Any]:
        trained = MODELS_DIR / "trained"
        if not trained.exists():
            return {}
        latest = sorted([p for p in trained.iterdir() if p.is_dir()], reverse=True)
        if not latest:
            return {}
        run = latest[0]
        metrics = {}
        mf = run / "metrics.txt"
        if mf.exists():
            for line in mf.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    metrics[k.strip()] = v.strip()
        metrics["run_id"] = run.name
        return metrics
