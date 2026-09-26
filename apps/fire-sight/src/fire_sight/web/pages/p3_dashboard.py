"""数据大屏 —— 趋势带 + 类别带 + 模型卡。"""

from __future__ import annotations

from pathlib import Path


def render(events_db=None) -> None:
    import streamlit as st

    from fire_sight.web.services.stats_service import StatsService
    from fire_sight.web.ui.charts import class_ratio_figure, daily_alert_figure
    from fire_sight.web.ui.theme import render_panel

    st.header("数据大屏")
    st.caption("告警趋势、类别构成与当前模型表现。")

    if events_db is None or not Path(events_db).exists():
        st.info("暂无事件数据，大屏将在首次告警后填充。")
        return

    stats = StatsService(events_db)
    ov = stats.overview()
    splits = (ov.get("dataset") or {}).get("splits") or {}

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("告警总数", ov.get("total_alerts", 0))
    k2.metric("误报率", f"{(ov.get('false_positive_rate') or 0) * 100:.1f}%")
    k3.metric("训练集", splits.get("train", 0))
    k4.metric("验证集", splits.get("val", 0))
    k5.metric("测试集", splits.get("test", 0))

    left, right = st.columns(2, gap="medium")
    daily = stats.daily_alerts(days=30)
    with left:
        render_panel("近 30 日告警趋势")
        st.plotly_chart(daily_alert_figure(daily), use_container_width=True)
    with right:
        render_panel("类别占比")
        st.plotly_chart(
            class_ratio_figure(ov.get("class_ratio") or {"fire": 0, "smoke": 0}),
            use_container_width=True,
        )

    model = ov.get("model") or {}
    render_panel("当前模型")
    if model:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("实验", str(model.get("run_id", ""))[:18])
        c2.metric("mAP50", str(model.get("mAP50", model.get("mAP@50", "—"))))
        c3.metric("精确率", str(model.get("precision", "—")))
        c4.metric("召回率", str(model.get("recall", "—")))
    else:
        st.info("尚未归档模型指标。")
