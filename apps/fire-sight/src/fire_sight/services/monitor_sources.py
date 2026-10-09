"""监控视频源清单：本机摄像头 + 现场 RTSP 设备。

为什么要有这份清单：视频源过去是一个自由文本框，现场的人得记住
`rtsp://user:pass@10.0.0.7:554/stream1` 这种长串，还得自己判断「序号 0」
对应哪台机器——换一台机器就全记错了。改成下拉之后，选项只能由后端给：
前端既不拼路径，也不猜设备号（`frame_source/factory.py` 才是唯一认这些串的地方）。

清单由两处合成：

- `FIRESIGHT_MONITOR_SOURCES` 环境变量——现场固定的监控设备。
  格式 `标签=地址`，多个用逗号或分号隔开：`东门=rtsp://10.0.0.7/live,北门=rtsp://10.0.0.8/live`。
  写地址而不写标签时用主机名兜底。沿用仓库既有的 env 约定（LLM 通道、事件库
  都是这么配的），现场加设备不用改代码。
- 本机摄像头——用 cv2 逐个序号试开，能打开的才算数。

两个必须守住的约束：

1. **探测会占用设备。** 监控正在跑的时候去试开同一个摄像头，等于跟流水线抢
   设备，现场表现是「正在看的画面突然黑了」。所以监控活跃时只返回配置项。
2. **探测不是免费的。** 每个试开要几十到几百毫秒，还会在 stderr 刷一串
   `Camera index out of range`。所以结果缓存 60s，并且连续两次打不开就停——
   编号通常是连续的，卡在中间某个序号上的机器极少。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from frame_source.sources.camera import STREAM_SCHEMES

logger = logging.getLogger(__name__)

#: 现场固定设备清单。逗号 / 分号 / 换行都当分隔符——现场配置手打，
#: 用什么分隔符全凭习惯，与其挑一种让人别扭，不如全认。
SOURCES_ENV = "FIRESIGHT_MONITOR_SOURCES"

#: 探测到第几个连续失败就收手。
MAX_PROBE_INDEX = 8
#: 连续这么多次打不开就认为后面没有了。
MAX_CONSECUTIVE_MISSES = 2
#: 清单缓存时长（秒）。够挡住刷新风暴，又不至于让人加完设备还等一分钟。
CACHE_TTL_SEC = 60.0

#: 摄像头试开的开销比读配置大得多，缓存期按倍数算。
_CAMERA_TTL_SEC = CACHE_TTL_SEC * 5

_cache: dict[str, Any] = {"at": 0.0, "cameras": []}
_lock = threading.Lock()


def _is_stream(value: str) -> bool:
    return value.lower().startswith(STREAM_SCHEMES)


def _label_for(url: str) -> str:
    """没写标签时用主机名兜底——总比一串 ip:port 好认。"""
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    return host.rsplit("@", 1)[-1] or url


def configured_sources() -> list[dict[str, Any]]:
    """`FIRESIGHT_MONITOR_SOURCES` 里配好的固定设备。

    顺序照配置里的顺序——现场是按「东门、南门…」的巡检顺序排的，
    重新排序等于替他们打乱巡检路线。
    """
    raw = (os.environ.get(SOURCES_ENV) or "").strip()
    if not raw:
        return []
    chunks = [c.strip() for c in raw.replace("\n", ",").replace(";", ",").split(",")]
    out: list[dict[str, Any]] = []
    seen = set()
    for chunk in chunks:
        if not chunk:
            continue
        label, sep, value = chunk.partition("=")
        value = value.strip() if sep else chunk
        label = label.strip()
        if not value:
            continue
        if not sep or not label:
            label = _label_for(value)
        if value in seen:
            continue
        seen.add(value)
        out.append(
            {
                "key": value,
                "label": label,
                "kind": "stream" if _is_stream(value) else "file",
                "detail": value,
            }
        )
    return out


def _probe_camera(index: int) -> dict[str, Any] | None:
    """试开一个摄像头序号。开不了返回 None。

    只在真能打开时才读分辨率：读属性会触发一次帧协商，
    对着一台不在的机器会白等一轮。
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover - cv2 是推理侧的硬依赖
        logger.debug("cv2 不可用，跳过摄像头探测")
        return None
    cap = None
    try:
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            return None
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    except Exception as exc:  # noqa: BLE001 - 枚举不该因单台设备异常而整体失败
        logger.debug("摄像头 %d 探测异常: %s", index, exc)
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001 - 释放失败无所谓，设备号已拿到
                pass
    detail = "×".join(str(v) for v in (width, height) if v)
    try:
        rate = f"{float(fps):.0f} fps" if float(fps) > 0 else ""
    except (TypeError, ValueError):
        rate = ""
    meta = " · ".join(p for p in (detail, rate) if p)
    return {
        "key": str(index),
        "label": f"本机摄像头 {index}",
        "kind": "camera",
        "detail": meta,
    }


def local_cameras(force: bool = False) -> list[dict[str, Any]]:
    """本机可打开的摄像头。带缓存。"""
    now = time.time()
    with _lock:
        fresh = (now - float(_cache["at"])) < _CAMERA_TTL_SEC
        if fresh and not force:
            return list(_cache["cameras"])
        # 探测要开设备，耗时可达数秒，锁放在这里直到结果写回：
        # 并发请求宁等一次也别让两拨人同时抢同一台摄像头。
        found: list[dict[str, Any]] = []
        misses = 0
        for index in range(MAX_PROBE_INDEX + 1):
            item = _probe_camera(index)
            if item is None:
                misses += 1
                if misses >= MAX_CONSECUTIVE_MISSES:
                    break
                continue
            misses = 0
            found.append(item)
        _cache["at"] = now
        _cache["cameras"] = found
        return list(found)


def list_monitor_sources(probe_cameras: bool = True) -> list[dict[str, Any]]:
    """视频源下拉的完整选项：先固定设备，后本机摄像头。

    `probe_cameras=False` 时只给配置项——监控进行中必须走这条路，
    否则试开摄像头会跟正在跑的流水线抢同一台设备。
    """
    out = configured_sources()
    if probe_cameras:
        out.extend(local_cameras())
    return out
