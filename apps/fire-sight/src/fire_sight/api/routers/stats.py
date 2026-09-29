"""月度火情统计（大屏）：近 N 月趋势 + 单月明细。"""

from __future__ import annotations

from fastapi import APIRouter

from fire_sight.api import deps
from fire_sight.api.schemas import MonthBucket, MonthDetail, MonthlyStats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/monthly", response_model=MonthlyStats)
def get_monthly(months: int = 12, month: str = "") -> MonthlyStats:
    """一次拉全：N 个月趋势桶 + 所选月（默认当月）的明细。

    趋势桶是**连续**的——空月份补 0，缺一根会让坐标轴把空窗画成正常间隔。
    """
    from fire_sight.services import StatsService

    stats = StatsService(deps.EVENTS_DB)
    buckets = stats.monthly_alerts(months=months, end_month=month)
    target = month[:7] if month else (buckets[-1]["month"] if buckets else "")
    return MonthlyStats(
        months=[MonthBucket(**b) for b in buckets],
        detail=MonthDetail(**stats.month_detail(target)),
    )
