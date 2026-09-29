"""火情分析报告接口：生成、轮询、历史、下载。

事件库路径经 `report_jobs.EVENTS_DB` **在调用时**取，而不是 `from ... import` 按值
拷贝——按值拷走的是导入那一刻的路径，测试把 `report_jobs.EVENTS_DB` 换成临时库后
这里仍会去读真实的 data/events/events.db，拒绝条件也就没测到真东西。
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from fire_sight.api import report_jobs
from fire_sight.api.schemas import ReportDetail, ReportJobInfo, ReportListItem, ReportRequest

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("", status_code=201, response_model=ReportJobInfo)
def create_report(req: ReportRequest) -> Dict[str, Any]:
    """起一次生成。无事件、未配通道都在这里拦掉——不让注定失败的任务白跑。"""
    from fire_sight.services.llm import LlmNotConfigured, resolve_settings
    from fire_sight.services.report_service import aggregate_events

    if req.end < req.start:
        raise HTTPException(status_code=400, detail="结束日期不能早于起始日期")

    summary = aggregate_events(report_jobs.EVENTS_DB, req.start, req.end)
    if summary["total"] == 0:
        raise HTTPException(
            status_code=400, detail="所选时间段没有告警记录，换一段有数据的区间再生成"
        )

    try:
        resolve_settings()
    except LlmNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return report_jobs.REPORTS.start(req.start, req.end)


@router.get("/jobs/{job_id}", response_model=ReportJobInfo)
def get_job(job_id: str) -> Dict[str, Any]:
    job = report_jobs.REPORTS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("", response_model=List[ReportListItem])
def list_reports() -> List[Dict[str, Any]]:
    from fire_sight.services.report_service import list_reports as _list

    return _list()


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(report_id: str, download: int = 0) -> Any:
    from fire_sight.services.report_service import is_valid_report_id, load_report

    # id 会拼进文件路径、也会插进 Content-Disposition：白名单之外一律 404，
    # 不给 `..%5C..%5C`（Windows 穿目录）、`.`、点号别名留任何缝隙
    if not is_valid_report_id(report_id):
        raise HTTPException(status_code=404, detail=f"报告不存在: {report_id}")

    try:
        data = load_report(report_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if download:
        return PlainTextResponse(
            data["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
        )
    return data
