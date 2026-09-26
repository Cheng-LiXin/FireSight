"""系统状态：工作区、GPU、任务锁、类别契约。"""

from __future__ import annotations

import sys

from fastapi import APIRouter

from fire_sight.api import deps
from fire_sight.api.schemas import ClassInfo, SystemStatus
from fire_sight.scene.class_map import CLASS_NAMES, NAME_CN

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status", response_model=SystemStatus)
def get_status() -> SystemStatus:
    """环境总览。事件库不存在时按 0 计，不报错——首次部署就是这个状态。"""
    event_count = 0
    if deps.EVENTS_DB.exists():
        try:
            from fire_sight.services import EventService

            event_count = EventService(deps.EVENTS_DB).count()
        except Exception:  # noqa: BLE001 - 状态接口不应因读库失败而 500
            event_count = 0

    active = deps.active_run()
    return SystemStatus(
        workspace=deps.workspace_info(),
        python_version=sys.version.split()[0],
        gpu=deps.gpu_info(),
        gpu_job=deps.GPU_LOCK.status(),
        trained_model_count=len(deps.archived_runs()),
        active_model=active.run_id if active else None,
        event_count=event_count,
        classes=[
            ClassInfo(id=i, name=n, name_cn=NAME_CN.get(n, n))
            for i, n in enumerate(CLASS_NAMES)
        ],
    )
