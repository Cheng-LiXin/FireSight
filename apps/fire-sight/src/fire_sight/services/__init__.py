"""数据服务层：路由只调这里，不直接 SQL / YOLO。"""

from fire_sight.services.event_service import EventService
from fire_sight.services.infer_service import InferService
from fire_sight.services.stats_service import StatsService

__all__ = ["EventService", "StatsService", "InferService"]
