"""仪表盘聚合：告警汇总、风险尺读数、类别占比、趋势、当前模型。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Query

from fire_sight.api import deps
from fire_sight.api.schemas import (
    AlertSummary,
    DailyPoint,
    ModelMetrics,
    Overview,
    RiskReading,
)

router = APIRouter(prefix="/api", tags=["overview"])

# 风险分档阈值（score 0..1）
_RISK_WATCH = 0.35
_RISK_WARN = 0.6
_RISK_ALERT = 0.85

# 24 小时内告警数达到该值即视为「量」已饱和
_VOLUME_SATURATION = 10


def _parse_ts(value: Any) -> datetime | None:
    """事件库 ts 为 ISO 字符串；解析失败返回 None 而非抛出。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # 事件库写入的是本地 naive 时间，统一按本地比较
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _risk_from(rows: List[Dict[str, Any]]) -> RiskReading:
    """风险尺读数。

    score 取「峰值置信度」与「告警量」的较大者：
    单次高置信度的明火，和持续多次的告警，都应把游标推向高位。
    """
    cutoff = datetime.now(timezone.utc).astimezone() - timedelta(hours=24)
    recent = [r for r in rows if (dt := _parse_ts(r.get("ts"))) and dt >= cutoff]

    if not recent:
        return RiskReading(
            score=0.0,
            level="normal",
            alerts_24h=0,
            peak_conf_24h=0.0,
            basis="近 24 小时无告警",
        )

    confs = [float(r.get("conf") or 0.0) for r in recent]
    peak = max(confs) if confs else 0.0
    volume = min(len(recent) / _VOLUME_SATURATION, 1.0)
    score = round(max(peak, volume), 3)

    if score >= _RISK_ALERT:
        level = "alert"
    elif score >= _RISK_WARN:
        level = "warn"
    elif score >= _RISK_WATCH:
        level = "watch"
    else:
        level = "normal"

    return RiskReading(
        score=score,
        level=level,
        alerts_24h=len(recent),
        peak_conf_24h=round(peak, 3),
        basis=f"近 24 小时 {len(recent)} 次告警，峰值置信度 {peak:.2f}",
    )


def _per_class_by_day(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """按日拆分类别计数。

    EventStore 的 labels 是逗号分隔字符串；StatsService.daily_alerts 只给总量，
    所以分类别的部分在这里自行聚合，避免为此改动既有服务。
    """
    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        dt = _parse_ts(r.get("ts"))
        if dt is None:
            continue
        bucket = out.setdefault(dt.date().isoformat(), {"smoke": 0, "fire": 0})
        for lab in (r.get("labels") or "").split(","):
            name = lab.strip()
            if name in bucket:
                bucket[name] += 1
    return out


def _model_metrics() -> Tuple[ModelMetrics, Dict[str, Any]]:
    """最新归档 run 的指标。无归档时返回 exists=False，前端据此显示空态。"""
    run = deps.active_run()
    if run is None:
        return ModelMetrics(exists=False), {}

    metrics_path = run.root / "metrics.txt"
    parsed: Dict[str, Any] = {}
    if metrics_path.exists():
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                parsed[k.strip()] = v.strip()
    return ModelMetrics(run_id=run.run_id, exists=True, metrics=parsed), parsed


@router.get("/overview", response_model=Overview)
def get_overview(days: int = Query(30, ge=1, le=365)) -> Overview:
    """仪表盘一次性拉取，避免前端打多个请求再拼装。

    `days` 必须夹上下界：下面按它逐日补点（`range(days - 1, -1, -1)`），
    无上界等于把一次查询变成无界内存与循环；`days=100000000` 更会一路
    算到 `OverflowError: date value out of range` 变成 500，而负值/0 会
    安静地回一个空序列（2026-10-09 代码审查 R2）。同仓 `routers/stats.py`
    的 `months` 就是正确范例。
    """
    from fire_sight.services import EventService, StatsService

    rows: List[Dict[str, Any]] = []
    daily: List[Dict[str, Any]] = []
    ratio: Dict[str, int] = {}
    total = 0
    fp_rate = 0.0

    if deps.EVENTS_DB.exists():
        svc = EventService(deps.EVENTS_DB)
        stats = StatsService(deps.EVENTS_DB)
        rows = svc.list_events(limit=1000)
        total = svc.count()
        daily = stats.daily_alerts(days=days)
        ratio = stats.class_ratio()
        fp_rate = stats.false_positive_rate()

    pending = sum(1 for r in rows if int(r.get("is_false_positive") or 0) == 0)
    model, _ = _model_metrics()

    per_class = _per_class_by_day(rows)
    conf_by_day = {d["date"]: d.get("avg_conf", 0.0) for d in daily}
    counts_by_day = {d["date"]: d.get("count", 0) for d in daily}

    # 时间序列必须连续：没有告警的那天要显式为 0。
    # 只回有数据的日子会让坐标轴把「空窗」画成正常间隔，等于对读者说谎。
    today = datetime.now().astimezone().date()
    daily_points = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        cls = per_class.get(day, {})
        daily_points.append(
            DailyPoint(
                date=day,
                count=counts_by_day.get(day, sum(cls.values())),
                smoke=cls.get("smoke", 0),
                fire=cls.get("fire", 0),
                avg_conf=conf_by_day.get(day, 0.0),
            )
        )

    return Overview(
        alerts=AlertSummary(
            total=total,
            pending=pending,
            false_positive_rate=round(float(fp_rate or 0.0), 4),
        ),
        risk=_risk_from(rows),
        class_ratio=ratio or {"smoke": 0, "fire": 0},
        daily=daily_points,
        model=model,
    )
