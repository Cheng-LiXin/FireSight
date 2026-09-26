"""识别检测页 —— 主作业区 + 指标竖列。"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def render(events_db=None) -> None:
    import cv2
    import streamlit as st

    from fire_sight.web.services.infer_service import InferService
    from fire_sight.web.ui.theme import render_panel
    from fire_sight_platform.common.paths import MODELS_DIR

    st.header("识别检测")
    st.caption("上传图像或指定目录，输出明火/烟雾框与置信度。")

    weights_list = sorted(MODELS_DIR.glob("trained/*/best.pt"))
    if not weights_list:
        st.warning("未找到训练权重。请先执行 fs-train 生成 best.pt。")
        return

    col_cfg, col_main, col_side = st.columns([1.05, 2.2, 0.85], gap="medium")

    with col_cfg:
        render_panel(
            "作业参数",
        )
        weights = st.selectbox(
            "权重",
            [str(p) for p in weights_list],
            index=len(weights_list) - 1,
        )
        conf = st.slider("置信度阈值", 0.05, 0.95, 0.25, 0.05)
        mode = st.radio("输入方式", ["单张图像", "批量目录"], horizontal=True)

    svc = InferService(weights=weights, conf=conf, device="0")
    model_label = Path(weights).parent.name

    with col_main:
        if mode == "单张图像":
            up = st.file_uploader("选择图像", type=["jpg", "jpeg", "png", "bmp"])
            if up is not None:
                data = np.frombuffer(up.getvalue(), dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                if img is None:
                    st.error("无法解码该图像，请更换文件。")
                    return
                with st.spinner("推理中..."):
                    result = svc.infer_array(img)
                left, right = st.columns(2)
                with left:
                    render_panel(
                        "原图",
                        f'<img src="data:image/jpeg;base64,{_b64(img)}" style="width:100%;"/>',
                    )
                with right:
                    render_panel(
                        "检测结果",
                        f'<img src="data:image/jpeg;base64,{_b64(result["annotated"])}" style="width:100%;"/>',
                    )
                if result["detections"]:
                    st.dataframe(
                        [
                            {
                                "类别": d["name"],
                                "置信度": round(d["conf"], 3),
                                "框 [x1,y1,x2,y2]": d["xyxy"],
                            }
                            for d in result["detections"]
                        ],
                        use_container_width=True,
                    )
                else:
                    st.info("本帧未检出明火或烟雾。")
                _cache_last(result, model_label)
        else:
            dir_path = st.text_input(
                "图像目录",
                value=str(
                    Path.cwd()
                    / "data"
                    / "processed"
                    / "D-Fire_train"
                    / "images"
                    / "val"
                ),
            )
            limit = st.number_input("最多处理张数", 1, 200, 10)
            if st.button("开始批量识别", type="primary"):
                p = Path(dir_path)
                if not p.exists():
                    st.error(f"目录不存在：{p}")
                    return
                with st.spinner("批量推理中..."):
                    rows = svc.infer_batch(p, limit=int(limit))
                st.dataframe(
                    [
                        {
                            "文件": Path(r["path"]).name,
                            "目标数": r["count"],
                            "类别": ",".join(r["classes"]) or "-",
                            "耗时 ms": r["elapsed_ms"],
                        }
                        for r in rows
                    ],
                    use_container_width=True,
                )
                _cache_last(
                    {
                        "count": sum(r["count"] for r in rows),
                        "classes": sorted({c for r in rows for c in r["classes"]}),
                        "elapsed_ms": round(
                            sum(r["elapsed_ms"] for r in rows) / max(len(rows), 1), 1
                        ),
                        "detections": [],
                    },
                    model_label,
                )

    with col_side:
        last = st.session_state.get("fs_last_infer", {})
        render_panel(
            "本次指标",
            f"""
            <div class="fs-cap">目标数</div>
            <div style="font-size:1.6rem;font-weight:650;color:#E8ECF1;font-variant-numeric:tabular-nums;">{last.get("count", "—")}</div>
            <div class="fs-cap" style="margin-top:0.55rem;">类别</div>
            <div style="color:#C9D1DE;">{", ".join(last.get("classes", [])) or "—"}</div>
            <div class="fs-cap" style="margin-top:0.55rem;">耗时 (ms)</div>
            <div style="font-size:1.15rem;color:#E8ECF1;font-variant-numeric:tabular-nums;">{last.get("elapsed_ms", "—")}</div>
            """,
        )
        render_panel(
            "说明",
            '<div class="fs-cap">阈值降低可提高召回、增加误报。告警复核请前往「历史火情」。</div>',
        )


def _b64(img) -> str:
    import base64

    import cv2

    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf).decode("ascii") if ok else ""


def _cache_last(result: dict, model_label: str) -> None:
    import streamlit as st

    st.session_state["fs_last_infer"] = {
        "count": result.get("count", 0),
        "classes": result.get("classes", []),
        "elapsed_ms": result.get("elapsed_ms", ""),
        "model": model_label,
    }
