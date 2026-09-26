"""事件查询与误报复核(包装 EventStore，页面不写 SQL)。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from fire_sight.alerting import EventStore


class EventService:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _store(self) -> EventStore:
        return EventStore(self.db_path)

    def list_events(
        self,
        keyword: str = "",
        label: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._store() as store:
            rows = store.list_events(limit=limit)
        if label:
            rows = [r for r in rows if label in (r.get("labels") or "")]
        if keyword:
            kw = keyword.lower()
            rows = [
                r
                for r in rows
                if kw in (r.get("message") or "").lower()
                or kw in (r.get("location") or "").lower()
                or kw in (r.get("event_id") or "").lower()
            ]
        return rows

    def mark_false_positive(self, event_id: str) -> None:
        with self._store() as store:
            store.mark_false_positive(event_id)

    def count(self) -> int:
        with self._store() as store:
            return store.count()

    def export_csv(self, out_path: Path | str, keyword: str = "") -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.list_events(keyword=keyword)
        fields = [
            "event_id",
            "ts",
            "frame_index",
            "labels",
            "conf",
            "location",
            "message",
            "clip_path",
            "is_false_positive",
        ]
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        return out_path
