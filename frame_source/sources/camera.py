"""摄像头 / RTSP 帧源。"""

from __future__ import annotations

import time
from typing import Iterator, Union

import cv2

from frame_source.core.base import FrameSource
from frame_source.core.registry import register_source
from frame_source.core.types import Frame

#: 网络流前缀——这些源最容易被卡死，必须走带超时的打开/读取路径。
STREAM_SCHEMES = ("rtsp://", "rtmp://", "http://", "https://")


def is_stream_url(device: Union[int, str]) -> bool:
    return isinstance(device, str) and device.lower().startswith(STREAM_SCHEMES)


class CameraSource(FrameSource):
    """device 可为摄像头序号(int/str) 或 rtsp/http URL。

    网络流与本地设备走不同的打开方式，因为它们的失败模式完全不同：

    - **网络流**：拔网线、对端进程崩了、NAT 会话过期时，`cap.read()` 会一直
      阻塞在 FFMPEG 内部，而停止信号只能置位 `threading.Event`、打断不了这次
      调用——监控状态就会永远停在「停止中」。所以这里显式指定 FFMPEG 后端并
      传 `CAP_PROP_OPEN_TIMEOUT_MSEC` / `CAP_PROP_READ_TIMEOUT_MSEC`，把阻塞上限
      从「无限」压成「超时值」，让上层有机会重新拿到控制权。
    - **本地设备序号**：保持 OpenCV 默认后端优先级（Windows 上是 MSMF/DSHOW），
      强制 FFMPEG 会让不少 USB 摄像头开不起来。为了不引入新故障，本地路径不加
      限制参数的 VideoCapture——它的 read() 通常以毫秒计返回。

    read_timeout 要明显大于「两帧之间的正常间隔」，否则弱网会被误判成断线。
    """

    #: 网络流打开超时（秒）。对着一个没人监听的端口时，FFMPEG 会一直等
    #: SDP/RTSP 握手；不设上限的话开流本身就可能挂死。
    OPEN_TIMEOUT_SEC = 10.0
    #: 网络流单次 read() 超时（秒）。这是「停止最多迟多久生效」的上界，
    #: 取 1s 是权衡结果：明显大于正常帧间隔（弱网不会误判），
    #: 又小到用户点完停止一两秒内就能看到状态变化。
    READ_TIMEOUT_SEC = 1.0
    #: 连续读失败多少次才判定「真的断了」。
    #: 1 次就判定太脆（弱网抖一下就重连，白白丢掉缓冲），
    #: 无限重试又会把断线检测变成死循环。这个值让断线在 ~3 秒内被认出来，
    #: 而单次读只阻塞 1 秒 —— 停止信号最迟 1 秒就能生效。
    MAX_READ_FAILURES = 3

    def __init__(
        self,
        device: Union[int, str] = 0,
        source_id: str = "",
        open_timeout_sec: float = OPEN_TIMEOUT_SEC,
        read_timeout_sec: float = READ_TIMEOUT_SEC,
        max_read_failures: int = MAX_READ_FAILURES,
    ) -> None:
        self.device = device
        super().__init__(source_id or f"camera-{device}")
        self.open_timeout_sec = float(open_timeout_sec)
        self.read_timeout_sec = float(read_timeout_sec)
        self.max_read_failures = max(1, int(max_read_failures))
        self.is_stream = is_stream_url(device)
        self._cap = self._open()
        try:
            if not self._cap.isOpened():
                # 打不开就当场失败。留着一个没打开的 capture，迭代只会一直
                # 读不出帧、被 max_read_failures 判成「断线」，界面上的表现是
                # 「监控已启动但永远没画面」——比起启动即报错，这种更难查。
                raise RuntimeError(f"摄像头打开失败: {device}")
            fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps > 0:
                self.fps = fps
        except Exception:
            # 打开失败/取属性失败都要把句柄还回去，否则摄像头会被一直占着，
            # 重试时打不开同一个设备。
            self.close()
            raise

    def _open(self):
        if self.is_stream:
            # 只有网络流才强制 FFMPEG + 超时参数。
            return cv2.VideoCapture(
                self.device,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.open_timeout_sec * 1000),
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout_sec * 1000),
                ],
            )
        return cv2.VideoCapture(self.device)

    def __iter__(self) -> Iterator[Frame]:
        i = 0
        failures = 0
        while True:
            # 停止信号放在每轮读之前查。read() 本身是阻塞调用、打断不了，
            # 所以这里能保证的是「上一次读返回后立刻生效」——配合 read_timeout，
            # 停止最迟迟一个读超时。真正卡死的最后一帧已经被超时挡住了。
            if self.stopped():
                return
            cap = self._cap
            if cap is None:  # 已 close()
                return
            ok, img = cap.read()
            if not ok:
                failures += 1
                if failures >= self.max_read_failures:
                    # 连续读不出来 = 源真的断了。结束迭代，把「断了」这件事
                    # 交回上层去处理（网络流退避重连、本地源直接失败）。
                    # 少量失败先当抖动放过：弱网下 read() 偶发失败很常见，
                    # 一次就判定断线会让监控不停重连、反复丢掉已攒的片段。
                    return
                continue
            failures = 0
            yield Frame(
                image=img,
                index=i,
                timestamp=time.time(),
                source_id=self.source_id,
                meta={"device": str(self.device)},
            )
            i += 1

    def close(self) -> None:
        if getattr(self, "_cap", None) is not None:
            self._cap.release()
            self._cap = None


@register_source("camera")
def _factory(device: Union[int, str] = 0, **kwargs) -> FrameSource:
    return CameraSource(device, **kwargs)
