"""告警事件：筛选分页、误报复核、片段回放、导出 CSV。

筛选在 SQL 里做（见 `EventStore._where`）。原先是「取前 N 条再在 Python 里过滤」，
那样第 2 页会重复或漏记录，总数也无从得知。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from fire_sight.api import deps
from fire_sight.api.schemas import AlertEventInfo, EventPage
from fire_sight.scene.class_map import CLASS_NAMES
from fire_sight.services import EventService
from fire_sight.services.event_service import rows_to_csv

router = APIRouter(prefix="/api/events", tags=["events"])

_STATUSES = ("all", "pending", "false_positive")
_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


def _service() -> EventService:
    return EventService(deps.EVENTS_DB)


def _validate(label: str, status: str) -> None:
    """显式拒绝写错的筛选值。

    不校验的话，`label=flame` 会安静地返回 0 条——用户看到的是「这段时间没有告警」，
    而事实是参数写错了。这类静默失败在本项目里已经出现过多次。
    """
    if label and label not in CLASS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"未知类别: {label}（只接受 {' / '.join(CLASS_NAMES)}）",
        )
    if status and status not in _STATUSES:
        raise HTTPException(
            status_code=400, detail=f"未知状态: {status}（只接受 {' / '.join(_STATUSES)}）"
        )


def _sql_status(status: str) -> str:
    """`all` 表示不加状态条件，交给 SQL 层处理成空串。"""
    return "" if status in ("", "all") else status


def _to_info(row: Dict[str, Any]) -> AlertEventInfo:
    labels = [s.strip() for s in (row.get("labels") or "").split(",") if s.strip()]
    clip = row.get("clip_path") or ""
    return AlertEventInfo(
        event_id=row.get("event_id") or "",
        ts=row.get("ts") or "",
        frame_index=row.get("frame_index"),
        labels=labels,
        conf=float(row.get("conf") or 0.0),
        location=row.get("location") or "",
        message=row.get("message") or "",
        # 库里可能留着已删除片段的路径，以磁盘为准而不是以字段为准
        has_clip=bool(clip) and Path(clip).is_file(),
        is_false_positive=bool(int(row.get("is_false_positive") or 0)),
    )


def _query(
    keyword: str, label: str, status: str, start: str, end: str, limit: int, offset: int
):
    return _service().list_events(
        keyword=keyword,
        label=label,
        status=_sql_status(status),
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=EventPage)
def list_events(
    keyword: str = Query("", max_length=100, description="匹配消息 / 位置 / 事件号"),
    label: str = Query("", description="类别，空表示不限"),
    status: str = Query("all", description="all / pending / false_positive"),
    start: str = Query("", description="起始日期 YYYY-MM-DD"),
    end: str = Query("", description="结束日期 YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> EventPage:
    """事件列表，新→旧。空库返回空数组而不是报错——首次部署就是这个状态。"""
    _validate(label, status)
    svc = _service()
    items = [
        _to_info(r)
        for r in _query(keyword, label, status, start, end, limit, offset)
    ]
    total = svc.count_events(
        keyword=keyword, label=label, status=_sql_status(status), start=start, end=end
    )
    return EventPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/export")
def export_events(
    keyword: str = Query("", max_length=100),
    label: str = Query(""),
    status: str = Query("all"),
    start: str = Query(""),
    end: str = Query(""),
) -> Response:
    """导出当前筛选下的全部事件为 CSV。

    直接返回字节流而不是先落盘再 FileResponse：导出是只读操作，不该在工作区
    留下临时文件。
    """
    _validate(label, status)
    rows = _query(keyword, label, status, start, end, 1_000_000, 0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=rows_to_csv(rows).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="firesight-events-{stamp}.csv"'
        },
    )


@router.post("/{event_id}/false-positive")
def mark_false_positive(event_id: str) -> Dict[str, Any]:
    """标记误报。事件不存在时 404——原实现无条件 UPDATE，前端会以为标上了。"""
    if not _service().mark_false_positive(event_id):
        raise HTTPException(status_code=404, detail=f"未找到事件: {event_id}")
    return {"event_id": event_id, "is_false_positive": True}


@router.get("/{event_id}/clip")
def get_clip(event_id: str) -> FileResponse:
    """取事件片段（告警前后若干帧）。"""
    row = _service().get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到事件: {event_id}")

    clip = row.get("clip_path") or ""
    if not clip:
        raise HTTPException(status_code=404, detail="该事件没有留存片段")

    # clip_path 是库里的绝对路径。不做归属校验就 FileResponse，
    # 这个接口就成了任意文件读取——与训练图件接口同一类问题。
    events_root = Path(deps.EVENTS_DB).parent.resolve()
    try:
        resolved = Path(clip).resolve()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="片段路径无法解析") from exc
    if events_root not in resolved.parents:
        raise HTTPException(status_code=400, detail="片段路径不在事件目录内")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="片段文件不存在（可能已被清理）")

    media = _MEDIA_TYPES.get(resolved.suffix.lower())
    if media is None:
        raise HTTPException(
            status_code=400, detail=f"不支持的片段格式: {resolved.suffix}"
        )
    return FileResponse(resolved, media_type=media)
