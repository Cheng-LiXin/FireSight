"""深色控制台主题 —— 消防机房仪表风(前端设计定稿)。"""

from __future__ import annotations

INK_950 = "#12151C"
INK_900 = "#171C27"
INK_800 = "#1C2230"
INK_700 = "#273142"
INK_500 = "#6B7385"
HAZE = "#C9D1DE"
GLASS = "#E8ECF1"
SIGNAL = "#E23D28"
EMBER = "#E8A317"
OK = "#3D9B78"

PAGE_TITLE = "FireSight"
PAGE_SUB = "火灾烟雾智能预警 · 值班控制台"
ACCENT = SIGNAL

FONT_SANS = (
    "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', "
    "'Segoe UI', system-ui, sans-serif"
)

CONSOLE_CSS = f"""
<style>
html, body, [class*="css"] {{
  font-family: {FONT_SANS};
  color: {HAZE};
}}
.stApp {{
  background: {INK_950};
  background-image:
    linear-gradient(rgba(107,115,133,0.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(107,115,133,0.07) 1px, transparent 1px);
  background-size: 28px 28px;
}}
section[data-testid="stSidebar"] {{
  background: {INK_900};
  border-right: 1px solid {INK_700};
}}
#fs-brand {{
  padding: 0.15rem 0 0.85rem 0;
  border-bottom: 1px solid {INK_700};
  margin-bottom: 1rem;
}}
#fs-brand .name {{
  font-size: 1.35rem;
  font-weight: 650;
  letter-spacing: 0.04em;
  color: {GLASS};
}}
#fs-brand .name em {{ color: {SIGNAL}; font-style: normal; }}
#fs-brand .sub {{
  margin-top: 0.3rem;
  font-size: 0.78rem;
  color: {INK_500};
}}
#fs-status {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem 1.15rem;
  align-items: center;
  padding: 0.75rem 1rem;
  margin: 0.15rem 0 1.15rem 0;
  background: {INK_800};
  border: 1px solid {INK_700};
  border-left: 3px solid {SIGNAL};
  border-radius: 3px;
  font-variant-numeric: tabular-nums;
}}
#fs-status .lamp {{
  width: 0.55rem; height: 0.55rem; border-radius: 50%;
  display: inline-block; margin-right: 0.45rem; vertical-align: middle;
  background: {OK};
  box-shadow: 0 0 0 3px rgba(61,155,120,0.18);
}}
#fs-status .lamp.warn {{
  background: {EMBER}; box-shadow: 0 0 0 3px rgba(232,163,23,0.2);
}}
#fs-status .lamp.alert {{
  background: {SIGNAL}; box-shadow: 0 0 0 3px rgba(226,61,40,0.22);
}}
#fs-status .item {{ color: {INK_500}; font-size: 0.82rem; }}
#fs-status .item strong {{
  color: {GLASS}; font-size: 1.05rem; font-weight: 600; margin-left: 0.35rem;
}}
#fs-status .item.alert strong {{ color: {SIGNAL}; }}
#fs-status .item.warn strong {{ color: {EMBER}; }}

.fs-panel {{
  background: {INK_800};
  border: 1px solid {INK_700};
  border-radius: 3px;
  padding: 1rem 1.1rem 0.85rem 1.1rem;
  margin-bottom: 0.95rem;
}}
.fs-panel .fs-h {{
  margin: 0 0 0.7rem 0;
  font-size: 0.92rem;
  font-weight: 600;
  color: {GLASS};
  letter-spacing: 0.03em;
}}
.fs-cap {{ color: {INK_500}; font-size: 0.8rem; line-height: 1.55; }}

h1, h2, h3 {{ color: {GLASS} !important; font-weight: 600; }}
.stMarkdown p, .stMarkdown li {{ color: {HAZE}; }}

.stButton > button {{
  background: {SIGNAL};
  color: #fff;
  border: 1px solid transparent;
  border-radius: 3px;
  font-weight: 600;
  padding: 0.45rem 1.05rem;
}}
.stButton > button:hover {{ filter: brightness(1.08); border-color: transparent; color: #fff; }}
.stTextInput input, .stSelectbox [data-baseweb="select"],
.stNumberInput input {{
  background: {INK_900} !important;
  color: {HAZE} !important;
}}
[data-testid="stMetric"] {{
  background: {INK_900};
  border: 1px solid {INK_700};
  border-radius: 3px;
  padding: 0.65rem 0.85rem;
}}
[data-testid="stMetricLabel"] {{ color: {INK_500} !important; font-size: 0.78rem; }}
[data-testid="stMetricValue"] {{ color: {GLASS} !important; font-variant-numeric: tabular-nums; }}
img {{ border-radius: 2px; }}
::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-thumb {{ background: {INK_700}; border-radius: 4px; }}
</style>
"""


def inject_console_css() -> None:
    import streamlit as st

    st.markdown(CONSOLE_CSS, unsafe_allow_html=True)


def render_status_bar(
    *,
    shift_alerts: int = 0,
    pending_review: int = 0,
    sources_online: int = 0,
    model_name: str = "-",
) -> None:
    """值班状态条：状态灯 + 运维指标。"""
    import streamlit as st

    if shift_alerts > 0:
        lamp = "alert"
    elif pending_review > 0:
        lamp = "warn"
    else:
        lamp = ""
    alert_cls = "alert" if shift_alerts else ""
    warn_cls = "warn" if pending_review and not shift_alerts else ""
    html = f"""
    <div id="fs-status">
      <span class="item"><span class="lamp {lamp}"></span>值班状态</span>
      <span class="item {alert_cls}">本班告警<strong>{shift_alerts}</strong></span>
      <span class="item {warn_cls}">待复核<strong>{pending_review}</strong></span>
      <span class="item">在线源<strong>{sources_online}</strong></span>
      <span class="item">当前权重<strong>{model_name}</strong></span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_panel(title: str, body_html: str = "") -> None:
    import streamlit as st

    st.markdown(
        f'<div class="fs-panel"><div class="fs-h">{title}</div>{body_html}</div>',
        unsafe_allow_html=True,
    )
