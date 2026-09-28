"""大屏聚合统计(只读，不改库)。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

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
