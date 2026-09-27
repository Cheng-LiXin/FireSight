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

    # ------------------------------------------------------------------
    # 筛选查询
    #
    # 筛选必须在 SQL 里做。原先是「取前 limit 条再在 Python 里过滤」——
    # 那样分页是错的（第 2 页会重复/漏掉记录），总数也无从得知，
    # 而且筛一个稀有类别时可能明明有匹配却返回空。
    # ------------------------------------------------------------------

    @staticmethod
    def _where(
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
    ) -> tuple:
        """拼 WHERE 子句与参数。全部走占位符，不做字符串插值。"""
        clauses: List[str] = []
        args: List = []

        if label:
            # labels 是逗号连接的列表；本项目只有 smoke / fire 两个短名，子串匹配够用
            clauses.append("labels LIKE ?")
            args.append(f"%{label}%")
        if status == "pending":
            clauses.append("COALESCE(is_false_positive, 0) = 0")
        elif status == "false_positive":
            clauses.append("COALESCE(is_false_positive, 0) = 1")
        if start:
            # ts 形如 2026-09-27T14:30:00，按日期前缀比较，避免时分秒的边界问题
            clauses.append("substr(ts, 1, 10) >= ?")
            args.append(start)
        if end:
            clauses.append("substr(ts, 1, 10) <= ?")
            args.append(end)
        if keyword:
            kw = f"%{keyword.lower()}%"
            clauses.append(
                "(LOWER(COALESCE(message, '')) LIKE ? "
                "OR LOWER(COALESCE(location, '')) LIKE ? "
                "OR LOWER(event_id) LIKE ?)"
            )
            args.extend([kw, kw, kw])

        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def query_events(
        self,
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict]:
        where, args = self._where(keyword, label, status, start, end)
        # ts 只到秒，同一秒内的多条必须靠第二个键定序，否则分页会漂
        cur = self._conn.execute(
            "SELECT * FROM alerts" + where + " ORDER BY ts DESC, event_id DESC LIMIT ? OFFSET ?",
            (*args, int(limit), int(offset)),
        )
        return [dict(r) for r in cur.fetchall()]

    def count_events(
        self,
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
    ) -> int:
        where, args = self._where(keyword, label, status, start, end)
        return int(self._conn.execute("SELECT COUNT(*) FROM alerts" + where, args).fetchone()[0])

    def get_event(self, event_id: str) -> Optional[dict]:
        cur = self._conn.execute("SELECT * FROM alerts WHERE event_id=?", (event_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def mark_false_positive(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE alerts SET is_false_positive=1 WHERE event_id=?", (event_id,)
        )
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
