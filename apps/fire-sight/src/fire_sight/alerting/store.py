"""告警事件 SQLite 库(低耦合: 只存 AlertEvent 字段)。"""

from __future__ import annotations

import sqlite3
import threading
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
    """事件库。**线程安全**：停止路径会把片段回填放到后台线程做（见 service 的
    `_finalize_stopped_clips`），所以这里必须允许跨线程使用同一个连接。

    为什么这件事曾是个真 bug：`sqlite3.connect` 默认 `check_same_thread=True`，
    跨线程一用就抛 `ProgrammingError: SQLite objects created in a thread can only
    be used in that same thread`。后果不是报错而是**静默丢数据**——片段文件已经
    写盘成功（几十 MB），但回填的 UPDATE 全被 except 吞掉，事件在界面上一辈子
    显示「未留存」，而磁盘上那份片段根本没人知道它存在。

    所以这里显式关掉该检查，并配一把锁串行化所有写操作：SQLite 自身在同一连接上
    是线程安全的（带模块级序列化），但我们的「读-改-写 + commit」要作为整体互斥，
    否则两个线程的 commit 交错。
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：允许后台回填线程复用主线程建的连接。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 跨线程共用一把连接时，写入必须整体互斥，否则 commit 会交错。
        self._write_lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._write_lock:
            self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def insert(self, event: AlertEvent, clip_path: str = "") -> None:
        with self._write_lock:
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
        with self._write_lock:
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
        with self._write_lock:
            cur = self._conn.execute(
                "SELECT * FROM alerts" + where
                + " ORDER BY ts DESC, event_id DESC LIMIT ? OFFSET ?",
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
        with self._write_lock:
            return int(
                self._conn.execute("SELECT COUNT(*) FROM alerts" + where, args).fetchone()[0]
            )

    def get_event(self, event_id: str) -> Optional[dict]:
        with self._write_lock:
            cur = self._conn.execute("SELECT * FROM alerts WHERE event_id=?", (event_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def mark_false_positive(self, event_id: str) -> None:
        with self._write_lock:
            self._conn.execute(
                "UPDATE alerts SET is_false_positive=1 WHERE event_id=?", (event_id,)
            )
            self._conn.commit()

    def set_clip(self, event_id: str, clip_path: str) -> None:
        """片段写完后回填路径。

        事件先入库（clip 为空）、片段后补齐：告警当帧就能在账册里看见，
        中途断电也不会丢事件——丢的最多是一段还没写完的片子。

        停止路径下这一步跑在后台线程（`clip-finalize`），所以必须跨线程可用，
        见类文档里那段 `check_same_thread` 的说明。
        """
        with self._write_lock:
            self._conn.execute(
                "UPDATE alerts SET clip_path=? WHERE event_id=?", (clip_path, event_id)
            )
            self._conn.commit()

    def count(self) -> int:
        with self._write_lock:
            return self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
