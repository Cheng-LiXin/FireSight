"""KPI 卡渲染(依赖 streamlit，但不拉业务数据)。"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple


def kpi_items(items: Sequence[Tuple[str, str]]) -> Sequence[Tuple[str, str]]:
    """校验并透传 KPI 数据，便于单测。"""
    out = []
    for item in items:
        if len(item) != 2:
            raise ValueError("KPI 项必须是 (label, value)")
        out.append((str(item[0]), str(item[1])))
    return out


def render_kpi_row(items: Sequence[Tuple[str, str]]):
    import streamlit as st

    items = kpi_items(items)
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)
