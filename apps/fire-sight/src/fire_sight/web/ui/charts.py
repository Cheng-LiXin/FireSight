"""Plotly 图表封装(纯函数，可测)。"""

from __future__ import annotations

from typing import Any, Dict, List


def daily_alert_figure(daily: List[Dict[str, Any]]):
    """近 N 日告警趋势：柱=次数，线=平均置信度。"""
    import plotly.graph_objects as go

    dates = [d.get("date") for d in daily]
    counts = [d.get("count", 0) for d in daily]
    confs = [d.get("avg_conf", 0) for d in daily]
    fig = go.Figure()
    fig.add_bar(x=dates, y=counts, name="告警次数", marker_color="#C0392B")
    fig.add_scatter(
        x=dates, y=confs, name="平均置信度", yaxis="y2", mode="lines+markers"
    )
    fig.update_layout(
        title="告警趋势",
        yaxis=dict(title="次数"),
        yaxis2=dict(title="置信度", overlaying="y", side="right", range=[0, 1]),
        legend=dict(orientation="h"),
        height=320,
        margin=dict(l=40, r=40, t=50, b=40),
    )
    return fig


def class_ratio_figure(ratio: Dict[str, int]):
    import plotly.graph_objects as go

    labels = list(ratio.keys())
    values = [ratio[k] for k in labels]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.55,
            marker=dict(colors=["#C0392B", "#7F8C8D"]),
        )
    )
    fig.update_layout(title="类别占比", height=320, margin=dict(l=20, r=20, t=50, b=20))
    return fig
