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
    is_false_positive INTEGER DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'pending'
);
"""

# 处置状态。只有这四个值合法，其余一律拒绝（见 EventStore.set_review_status）。
#
# 为什么在 is_false_positive 之外还要一列：那一列只回答「是不是误报」，
# 回答不了「看没看过、处置了没」。值班的人要能区分「刚响还没看」和
# 「确认过了但火还没灭」。两列并存，见 set_review_status 的同步说明。
REVIEW_STATUSES = ("pending", "confirmed", "false_positive", "resolved")
DEFAULT_REVIEW_STATUS = "pending"

# 老库补列后必须按误报位回填：新列的默认值是 'pending'，若不回填，
# 老库里标过误报的行会变成「review_status=pending 且 is_false_positive=1」
# 这种自相矛盾的记录——界面按状态筛「误报」查不到它，统计却算得进去。
_BACKFILL_FP = (
    "UPDATE alerts SET review_status='false_positive' "
    "WHERE COALESCE(is_false_positive, 0)=1 AND review_status<>'false_positive'"
)


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
        self._ensure_review_status_column()
        self._conn.commit()

    # ------------------------------------------------------------------
    # 迁移
    # ------------------------------------------------------------------

    def _ensure_review_status_column(self) -> None:
        """给已有库补上 review_status 列，并回填老行的状态。

        `CREATE TABLE IF NOT EXISTS` 对已存在的表是**完全的空操作**——
        老库不会因为改了 _SCHEMA 就多出这一列。所以必须显式 ALTER TABLE。

        幂等：先看列在不在，补过就不补。本项目此前没有任何迁移机制，
        这是第一个；它必须能反复执行，因为 EventStore 每次请求都会新建一个
        实例，没有「只在升级时跑一次」的机会。
        """
        cols = {
            str(r["name"])
            for r in self._conn.execute("PRAGMA table_info(alerts)").fetchall()
        }
        if "review_status" not in cols:
            self._conn.execute(
                f"ALTER TABLE alerts ADD COLUMN review_status TEXT NOT NULL "
                f"DEFAULT '{DEFAULT_REVIEW_STATUS}'"
            )
        self._conn.execute(_BACKFILL_FP)

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
        # 状态直接查 review_status 列，不在 Python 里换算：
        # 四态里 pending/confirmed/resolved 三者 is_false_positive 都是 0，
        # 用布尔位表达不出来，只能有一列是状态的真身。
        if status in REVIEW_STATUSES:
            clauses.append("review_status = ?")
            args.append(status)
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
        """标为误报：状态与误报位一起写。

        两列必须同时改。误报位是统计口径（误报率、月度图表、大屏都读它），
        状态列是界面口径；只改其中一列，两者立刻互相矛盾。
        """
        with self._write_lock:
            self._conn.execute(
                "UPDATE alerts SET is_false_positive=1, review_status='false_positive' "
                "WHERE event_id=?",
                (event_id,),
            )
            self._conn.commit()

    def set_review_status(self, event_id: str, status: str) -> None:
        """改处置状态，并让误报位跟着走。

        误报位的取值由状态决定，不由调用方传：这样就不可能出现
        「状态=误报但误报位=0」或「撤销了状态却没清误报位」这类偏差。
        取消误报回到 pending（还没人看过，所以不是「已处置」）。
        """
        if status not in REVIEW_STATUSES:
            raise ValueError(f"未知处置状态: {status}")
        fp = 1 if status == "false_positive" else 0
        with self._write_lock:
            self._conn.execute(
                "UPDATE alerts SET review_status=?, is_false_positive=? WHERE event_id=?",
                (status, fp, event_id),
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
