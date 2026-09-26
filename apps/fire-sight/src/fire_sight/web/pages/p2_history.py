"""历史火情页 —— 处置带 + 片段复核。"""

from __future__ import annotations

from pathlib import Path


def render(events_db=None) -> None:
    import streamlit as st

    from fire_sight.web.services.event_service import EventService

    st.header("历史火情")
    st.caption("查询告警、回放留存片段、标记误报。")

    if events_db is None or not Path(events_db).exists():
        st.info("暂无事件记录。运行 fs-infer 或识别页检测后将写入事件库。")
        return

    svc = EventService(events_db)

    bar = st.columns([1.4, 1, 1, 1, 1])
    keyword = bar[0].text_input("关键词", placeholder="位置 / 消息 / 编号")
    label = bar[1].selectbox("类别", ["全部", "fire", "smoke"])
    fp_filter = bar[2].selectbox("处置", ["全部", "待复核", "已标误报"])
    limit = bar[3].number_input("条数", 10, 500, 50)
    if bar[4].button("导出 CSV", use_container_width=True):
        out = Path(events_db).parent / "alerts_export.csv"
        path = svc.export_csv(out, keyword=keyword)
        st.toast(f"已导出 {path}")

    rows = svc.list_events(
        keyword=keyword, label="" if label == "全部" else label, limit=int(limit)
    )
    if fp_filter == "待复核":
        rows = [r for r in rows if not int(r.get("is_false_positive") or 0)]
    elif fp_filter == "已标误报":
        rows = [r for r in rows if int(r.get("is_false_positive") or 0) == 1]

    st.markdown(
        f'<div class="fs-cap" style="margin:0.4rem 0 0.8rem 0;">共 {len(rows)} 条</div>',
        unsafe_allow_html=True,
    )

    if not rows:
        st.info("没有符合条件的记录。")
        return

    for r in rows:
        fp = int(r.get("is_false_positive") or 0)
        with st.container(border=True):
            top = st.columns([2.6, 1.6, 1.2, 1.2])
            top[0].markdown(f"**{r.get('message') or r.get('event_id')}**")
            top[1].caption(f"{r.get('ts')} · {r.get('location') or '—'}")
            top[2].caption(f"conf {r.get('conf')}")
            top[3].caption("已标误报" if fp else "待复核")
            clip = r.get("clip_path") or ""
            if clip and Path(clip).exists():
                st.video(clip)
            if not fp and st.button("标记误报", key=f"fp_{r.get('event_id')}"):
                svc.mark_false_positive(r["event_id"])
                st.rerun()
