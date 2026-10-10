"""事件查询与误报复核(包装 EventStore，页面不写 SQL)。"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fire_sight.alerting import REVIEW_STATUSES, EventStore

# 导出列。顺序即表格列顺序，改动要同步 REST-API 文档。
_EXPORT_FIELDS = [
    "event_id",
    "ts",
    "frame_index",
    "labels",
    "conf",
    "location",
    "message",
    "clip_path",
    "is_false_positive",
    "review_status",
]


def rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    """把事件行渲染成 CSV 文本（含表头）。

    带 BOM：这份 CSV 的归宿大概率是 Excel，没有 BOM 时中文列会显示成乱码。
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return "\ufeff" + buf.getvalue()


class EventService:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _store(self) -> EventStore:
        return EventStore(self.db_path)

    def list_events(
        self,
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """筛选 + 分页。筛选在 SQL 里做，见 EventStore._where 的注释。"""
        with self._store() as store:
            return store.query_events(
                keyword=keyword,
                label=label,
                status=status,
                start=start,
                end=end,
                limit=limit,
                offset=offset,
            )

    def count_events(
        self,
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
    ) -> int:
        with self._store() as store:
            return store.count_events(
                keyword=keyword, label=label, status=status, start=start, end=end
            )

    def list_page(
        self,
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """一次翻页要的两件事：行 + 总数，共用一条连接。

        列表页必须同时给出记录与总数。此前路由为这两件事各建一个 EventService、
        每个再各建一个 EventStore，而 `EventStore.__init__` 里带着建表脚本与老库
        回填 UPDATE——于是一次翻页 = 2 条 SQLite 连接 + 2 次建表 + 2 次全表
        UPDATE，每个用户每次翻页都重来（2026-10-09 代码审查 R1）。

        两条查询在同一条连接上串行执行，语义与分开做完全一致。
        """
        with self._store() as store:
            rows = store.query_events(
                keyword=keyword,
                label=label,
                status=status,
                start=start,
                end=end,
                limit=limit,
                offset=offset,
            )
            total = store.count_events(
                keyword=keyword, label=label, status=status, start=start, end=end
            )
        return rows, total

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._store() as store:
            return store.get_event(event_id)

    def mark_false_positive(self, event_id: str) -> bool:
        """标记误报。返回是否命中——不存在时返回 False，由路由转成 404。

        原实现无条件执行 UPDATE，影响行数为 0 也当成功，前端会以为标上了。
        """
        with self._store() as store:
            if store.get_event(event_id) is None:
                return False
            store.mark_false_positive(event_id)
            return True

    def set_review_status(self, event_id: str, status: str) -> bool:
        """改处置状态。返回是否命中——不存在时返回 False，由路由转成 404。

        与 mark_false_positive 同一条纪律：先确认事件存在再写。否则
        UPDATE 影响 0 行、接口回 200，界面上的按钮却不会消失——
        用户会以为点不动是系统卡了，而事实是这条记录早被清理。
        """
        if status not in REVIEW_STATUSES:
            raise ValueError(f"未知处置状态: {status}")
        with self._store() as store:
            if store.get_event(event_id) is None:
                return False
            store.set_review_status(event_id, status)
            return True

    def count(self) -> int:
        with self._store() as store:
            return store.count()

    def export_csv(
        self,
        out_path: Path | str,
        keyword: str = "",
        label: str = "",
        status: str = "",
        start: str = "",
        end: str = "",
    ) -> Path:
        """把（当前筛选下的）全部事件写成 CSV 文件。

        默认 limit 走 list_events 的 200 不够用——导出要的是全集，
        所以这里显式传一个大 limit。
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.list_events(
            keyword=keyword,
            label=label,
            status=status,
            start=start,
            end=end,
            limit=1_000_000,
        )
        # newline="" 必须给：csv 自己输出 \r\n，若让文本层再做一次换行翻译，
        # Windows 上会变成 \r\r\n。utf-8（配合 rows_to_csv 的 BOM）保证 Excel 不乱码。
        with out_path.open("w", encoding="utf-8", newline="") as f:
            f.write(rows_to_csv(rows))
        return out_path
