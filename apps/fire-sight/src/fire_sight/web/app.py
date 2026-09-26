"""FireSight Web 入口（深色控制台）。

    fs-web
    streamlit run apps/fire-sight/src/fire_sight/web/app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from fire_sight.web.ui.theme import (
    PAGE_SUB,
    PAGE_TITLE,
    inject_console_css,
    render_status_bar,
)
from fire_sight_platform.common.paths import DATA_DIR, MODELS_DIR

st.set_page_config(
    page_title=f"{PAGE_TITLE} · 值班控制台",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_console_css()

st.sidebar.markdown(
    f"""
    <div id="fs-brand">
      <div class="name"><em>Fire</em>Sight</div>
      <div class="sub">{PAGE_SUB}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

page = st.sidebar.radio("通道", ["识别检测", "历史火情", "数据大屏"], index=0)
st.sidebar.caption("对象明火 / 烟雾 · YOLO 双类检测")

EVENTS_DB = DATA_DIR / "events" / "events.db"


def _shift_metrics():
    """状态条数据：本班/待复核来自事件库。"""
    alerts = pending = 0
    if EVENTS_DB.exists():
        try:
            from fire_sight.alerting import EventStore

            with EventStore(EVENTS_DB) as store:
                rows = store.list_events(limit=500)
            alerts = len(rows)
            pending = sum(1 for r in rows if not int(r.get("is_false_positive") or 0))
            # 待复核口径：有事件且未标误报的近期项，用未标记误报数展示「待处置」
            pending = sum(1 for r in rows if int(r.get("is_false_positive") or 0) == 0)
        except Exception:
            pass
    weights = sorted(MODELS_DIR.glob("trained/*/best.pt"))
    model_name = weights[-1].parent.name if weights else "未训练"
    return alerts, pending, model_name


shift_alerts, pending_review, model_name = _shift_metrics()
render_status_bar(
    shift_alerts=shift_alerts,
    pending_review=pending_review,
    sources_online=1,
    model_name=model_name,
)

if page == "识别检测":
    from fire_sight.web.pages import p1_detect

    p1_detect.render(events_db=EVENTS_DB)
elif page == "历史火情":
    from fire_sight.web.pages import p2_history

    p2_history.render(events_db=EVENTS_DB)
else:
    from fire_sight.web.pages import p3_dashboard

    p3_dashboard.render(events_db=EVENTS_DB)
