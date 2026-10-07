"""RTSP 常驻监控接口：启停、状态、MJPEG 推流。"""

from __future__ import annotations

import time
from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from fire_sight.api import deps
from fire_sight.api.monitor_job import MONITOR

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# MJPEG 推流节流：10fps 够值班看，也不把带宽和 CPU 吃满
STREAM_FPS = 10
# 没有新帧时的空转间隔，避免 while True 烧 CPU
IDLE_SLEEP_SEC = 0.05
# /monitor/stop 在返回前等终态的耐心值（秒）。
# 要略大于帧源的单次读超时（CameraSource.READ_TIMEOUT_SEC = 1.0s），
# 否则请求会先于收流结束返回，前端还是只能看到「停止中」。
STOP_WAIT_SEC = 3.0


class MonitorStart(BaseModel):
    source: str = Field(min_length=1, description="rtsp:// 或本地视频/图片目录")
    camera_name: str = Field("", description="位置名，进事件与 TTS 文案")
    conf: float = Field(0.25, ge=0.0, le=1.0)
    iou: float = Field(0.6, ge=0.0, le=1.0)
    device: str = "0"
    enable_tts: bool = True
    clip_before_sec: float = Field(30.0, ge=0.0, le=600.0)
    clip_after_sec: float = Field(30.0, ge=0.0, le=600.0)
    weights: str = ""


@router.post("/start")
def start_monitor(req: MonitorStart) -> dict:
    """启动常驻监控。GPU 被训练占着时 409——训练优先，监控不排队抢卡。"""
    try:
        return MONITOR.start(
            source=req.source,
            camera_name=req.camera_name,
            device=req.device,
            conf=req.conf,
            iou=req.iou,
            enable_tts=req.enable_tts,
            clip_before_sec=req.clip_before_sec,
            clip_after_sec=req.clip_after_sec,
            weights=req.weights,
        )
    except deps.GpuBusy as exc:
        raise HTTPException(
            status_code=409, detail=f"显存正被「{exc.holder or '其他任务'}」占用，请先停掉它"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stop")
def stop_monitor() -> dict:
    """请求停止；已经没在跑时返回 stopped=false，不报错。

    在请求内等一小会儿等到终态再返回：以前这里只置位信号就返回一份 `stopping`，
    前端紧接着刷新拿到的必然是「停止中」，于是界面上要一直挂着这个状态直到下次
    轮询——用户说的「状态在停止中滞留很久」就是这么来的。停止链路现在有界
    （读帧超时 + 不再等片段编码），这里等一下就能直接给出准信。
    """
    stopped = MONITOR.stop()
    if stopped:
        MONITOR.wait_stopped(timeout=STOP_WAIT_SEC)
    return {"stopped": stopped, **MONITOR.status()}


@router.get("/status")
def monitor_status() -> dict:
    return MONITOR.status()


def _mjpeg() -> Iterator[bytes]:
    """多段 JPEG 流。浏览器 <img src> 直接消费，零依赖、低延迟。"""
    last: Optional[bytes] = None
    frame_interval = 1.0 / STREAM_FPS
    while True:
        jpeg = MONITOR.latest_jpeg()
        if jpeg is None or jpeg == last:
            # 没有新帧就等一下：源断线时这里自然降频，不会空转烧 CPU
            time.sleep(IDLE_SLEEP_SEC)
            continue
        last = jpeg
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
            + jpeg
            + b"\r\n"
        )
        time.sleep(frame_interval)


@router.get("/stream")
def monitor_stream() -> StreamingResponse:
    return StreamingResponse(
        _mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
