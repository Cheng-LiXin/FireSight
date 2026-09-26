"""告警事件 SQLite 库(低耦合: 只存 AlertEvent 字段)。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fire_sight.scene.types import AlertEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    event_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    frame_index INTEGER,
    labels TEXT,
    conf REAL,
    location TEXT,
    message TEXT,
    clip_path TEXT,
    is_false_positive INTEGER DEFAULT 0
);
"""


class EventStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def insert(self, event: AlertEvent, clip_path: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO alerts "
            "(event_id, ts, frame_index, labels, conf, location, message, clip_path) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                datetime.now().isoformat(timespec="seconds"),
                event.frame_index,
                ",".join(event.labels),
                float(event.conf),
                event.location,
                event.message,
                clip_path,
            ),
        )
        self._conn.commit()

    def list_events(self, limit: int = 100) -> List[dict]:
        cur = self._conn.execute(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    def mark_false_positive(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE alerts SET is_false_positive=1 WHERE event_id=?", (event_id,)
        )
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
