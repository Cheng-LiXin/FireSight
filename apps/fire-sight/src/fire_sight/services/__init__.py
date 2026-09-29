"""数据服务层：路由只调这里，不直接 SQL / YOLO。"""

from fire_sight.services.event_service import EventService
from fire_sight.services.infer_service import InferService
from fire_sight.services.llm import LlmError, LlmNotConfigured, resolve_client, resolve_settings
from fire_sight.services.model_service import ActiveModelStore, ActiveState
from fire_sight.services.recycle_service import Removed, recycle
from fire_sight.services.report_service import (
    aggregate_events,
    build_prompt,
    list_reports,
    load_report,
    save_report,
)
from fire_sight.services.stats_service import StatsService

__all__ = [
    "ActiveModelStore",
    "ActiveState",
    "EventService",
    "LlmError",
    "LlmNotConfigured",
    "StatsService",
    "InferService",
    "Removed",
    "aggregate_events",
    "build_prompt",
    "list_reports",
    "load_report",
    "resolve_client",
    "resolve_settings",
    "recycle",
    "save_report",
]
