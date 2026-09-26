"""Web 服务层：页面只调这里，不直接 SQL/YOLO。"""

from fire_sight.web.services.event_service import EventService
from fire_sight.web.services.stats_service import StatsService
from fire_sight.web.services.infer_service import InferService

__all__ = ["EventService", "StatsService", "InferService"]
