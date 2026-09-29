"""月度火情统计（大屏）：近 N 月趋势 + 单月明细。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from fire_sight.api import deps
from fire_sight.api.schemas import MonthBucket, MonthDetail, MonthlyStats

router = APIRouter(prefix="/api/stats", tags=["stats"])

# month 形如 2026-09。形状对了下游 strptime 才不会抛 ValueError 变成 500；
# 月份限定 01-12，否则 2026-13 一样炸。空串 = 默认当月。
_MONTH_RE = r"^(|\d{4}-(0[1-9]|1[0-2]))$"


@router.get("/monthly", response_model=MonthlyStats)
def get_monthly(
    months: int = Query(12, ge=1, le=120),
    month: str = Query("", pattern=_MONTH_RE),
) -> MonthlyStats:
    """一次拉全：N 个月趋势桶 + 所选月（默认当月）的明细。

    趋势桶是**连续**的——空月份补 0，缺一根会让坐标轴把空窗画成正常间隔。
    `months` 封顶：`_month_range` 按它建列表，不设上限就等于把一次查询变成
    无界内存分配。
    """
    from fire_sight.services import StatsService

    stats = StatsService(deps.EVENTS_DB)
    buckets = stats.monthly_alerts(months=months, end_month=month)
    target = month if month else (buckets[-1]["month"] if buckets else "")
    return MonthlyStats(
        months=[MonthBucket(**b) for b in buckets],
        detail=MonthDetail(**stats.month_detail(target)),
    )
