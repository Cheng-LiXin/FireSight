"""frame_source 模块测试：图片夹 / 视频 / 工厂识别。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def image_dir(tmp_path):
    import cv2

    d = tmp_path / "imgs"
    d.mkdir()
    for i in range(3):
        img = np.zeros((32, 48, 3), dtype=np.uint8)
        img[:, :] = (i * 40, 60, 80)
        cv2.imwrite(str(d / f"f{i}.png"), img)
    return d


def test_image_dir_iterates_all(image_dir):
    from frame_source import create_frame_source

    with create_frame_source(str(image_dir)) as src:
        frames = list(src)
    assert len(frames) == 3
    assert frames[0].image.shape == (32, 48, 3)
    assert frames[1].index == 1


def test_factory_recognizes_dir_and_video(image_dir, tmp_path):
    import cv2

    from frame_source import create_frame_source

    assert type(create_frame_source(str(image_dir))).__name__ == "ImageDirSource"

    vp = tmp_path / "clip.mp4"
    w = cv2.VideoWriter(str(vp), cv2.VideoWriter_fourcc(*"mp4v"), 5, (48, 32))
    for i in range(4):
        w.write(np.full((32, 48, 3), i * 10, dtype=np.uint8))
    w.release()
    assert type(create_frame_source(str(vp))).__name__ == "VideoFileSource"
    with create_frame_source(str(vp)) as src:
        assert len(list(src)) == 4


def test_read_one(image_dir):
    from frame_source import create_frame_source

    src = create_frame_source(str(image_dir))
    f = src.read_one()
    assert f is not None
    src.close()


def test_camera_type_registered():
    from frame_source.core.registry import list_sources

    names = list_sources()
    assert "camera" in names
    assert "video" in names
    assert "image_dir" in names


# ----------------------------------------------------------------------
# 停止信号：RTSP 断线时「停止」必须真的停得下来
# ----------------------------------------------------------------------


def test_stop_event_halts_iteration(image_dir):
    """置位停止信号后，源不该再吐出帧。

    停止链路曾经卡在「停止中」，最底下的一环就是源不知道有人在喊停：
    事件置了位，迭代却照旧往下走。
    """
    import threading

    from frame_source import create_frame_source

    stop = threading.Event()
    src = create_frame_source(str(image_dir), stop_event=stop)
    got = []
    for f in src:
        got.append(f)
        if len(got) == 1:
            stop.set()
    src.close()
    assert len(got) == 1


def test_stop_event_preset_stops_before_first_frame(image_dir):
    import threading

    from frame_source import create_frame_source

    stop = threading.Event()
    stop.set()
    src = create_frame_source(str(image_dir), stop_event=stop)
    assert list(src) == []
    src.close()


def test_no_stop_event_keeps_full_iteration(image_dir):
    """不传停止信号时行为必须与从前一致——这是兼容性底线。"""
    from frame_source import create_frame_source

    with create_frame_source(str(image_dir)) as src:
        assert len(list(src)) == 3


def test_camera_stream_uses_timeouts():
    """网络流必须带读写超时，否则 cap.read() 会永久阻塞，停止信号打不断它。"""
    from frame_source.sources.camera import CameraSource, is_stream_url

    assert is_stream_url("rtsp://1.2.3.4/live")
    assert is_stream_url("http://1.2.3.4/live")
    assert not is_stream_url(0)
    assert not is_stream_url("3")
    # 超时值要有界：停止延迟的上界就是它
    assert 0 < CameraSource.READ_TIMEOUT_SEC <= 5.0
    assert 0 < CameraSource.OPEN_TIMEOUT_SEC <= 30.0
    assert CameraSource.MAX_READ_FAILURES >= 1


def _fake_camera(read_results, max_read_failures=3):
    """构造一个不碰 cv2.VideoCapture 的 CameraSource（单元测试不该依赖真摄像头）。"""
    import numpy as np

    from frame_source.core.base import FrameSource
    from frame_source.sources.camera import CameraSource

    class _FakeCap:
        def read(self):
            return read_results.pop(0) if read_results else (False, None)

        def release(self):
            pass

        def get(self, _prop):
            return 0.0

    src = CameraSource.__new__(CameraSource)
    FrameSource.__init__(src, "c")
    src.device = 0
    src.max_read_failures = max_read_failures
    src._cap = _FakeCap()
    return src, np.zeros((8, 8, 3), dtype=np.uint8)


def test_camera_tolerates_transient_read_failures():
    """偶发一次读失败不能当成断线——弱网抖动不该让监控不停重连、丢掉已攒片段。"""
    from frame_source.sources.camera import CameraSource

    import numpy as np

    img = np.zeros((8, 8, 3), dtype=np.uint8)
    src, _ = _fake_camera([(False, None), (True, img), (True, img)])
    got = list(src)
    assert len(got) == 2, "一次抖动后应继续读到后续帧"


def test_camera_gives_up_after_repeated_failures():
    """连续读不出来就必须结束迭代，把「断了」交回上层（否则变成忙等死循环）。"""
    src, _ = _fake_camera([])
    assert list(src) == []


def test_camera_iteration_respects_stop_event():
    import threading

    import numpy as np

    img = np.zeros((8, 8, 3), dtype=np.uint8)
    # 永远能读到帧的源：停止信号是唯一的出口
    src, _ = _fake_camera([(True, img)] * 50)
    stop = threading.Event()
    src.with_stop_event(stop)
    got = []
    for f in src:
        got.append(f)
        if len(got) == 2:
            stop.set()
    assert len(got) == 2
