"""alerting 模块测试：事件库、片段缓冲、TTS 降级。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP = ROOT / "apps" / "fire-sight" / "src"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def test_event_store_roundtrip(tmp_path):
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    db = tmp_path / "events.db"
    ev = AlertEvent(
        event_id="evt-1",
        frame_index=10,
        labels=["smoke"],
        conf=0.88,
        location="东门",
        message="检测到烟雾",
    )
    with EventStore(db) as store:
        store.insert(ev, clip_path=str(tmp_path / "c.mp4"))
        rows = store.list_events()
        assert store.count() == 1
        assert rows[0]["labels"] == "smoke"
        store.mark_false_positive("evt-1")
        assert store.list_events()[0]["is_false_positive"] == 1


def test_ring_buffer_seconds_sizing_and_write_clip(tmp_path):
    import cv2

    from fire_sight.alerting import RingClipBuffer
    from fire_sight.alerting.clip_buffer import write_clip

    # 容量按「秒 × fps」换算，不是调用方自己算帧数——需求口径是秒
    buf = RingClipBuffer(before_sec=0.5, fps=10)
    assert buf.max_frames == 5

    for i in range(8):
        img = np.full((24, 32, 3), i * 20, dtype=np.uint8)
        buf.push(i, float(i), img)
    frames = buf.snapshot()
    assert len(frames) == 5  # 只保留最近
    # JPEG 是有损的，但纯色块应当接近原值（差 3 个灰阶以内）
    for got, want in zip(frames, (60, 80, 100, 120, 140)):
        assert abs(float(got.mean()) - want) < 3
        assert got.shape == (24, 32, 3)

    out = write_clip(frames, tmp_path / "clip.mp4", fps=5)

    # 返回的是**实际**路径，后缀可能不是 .mp4——浏览器播不了 mp4v，
    # write_clip 会按可用编码挑一个（webm / mp4）
    assert out.exists() and out.stat().st_size > 0
    assert out.suffix in {".webm", ".mp4"}

    # 关键：必须真的可解码。只信 isOpened() 会漏掉「写出空容器」那种静默失败
    cap = cv2.VideoCapture(str(out))
    got = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        got += 1
    cap.release()
    assert got == len(frames), "写出的片段回读帧数不符"


def test_ring_buffer_stores_compressed_frames():
    """30 秒 × 30fps ≈ 900 帧，存原始 BGR 会到 GB 级；必须压着放。"""
    from fire_sight.alerting import RingClipBuffer

    buf = RingClipBuffer(before_sec=1.0, fps=10)
    rng = np.random.default_rng(0)
    for i in range(10):
        # 带噪声的渐变图：接近真实画面，JPEG 才有压缩空间
        img = np.linspace(0, 255, 64 * 64 * 3, dtype=np.uint8).reshape(64, 64, 3)
        img = np.clip(img.astype(np.int16) + rng.integers(-20, 20, img.shape), 0, 255).astype(
            np.uint8
        )
        buf.push(i, float(i), img)

    raw_bytes = 10 * 64 * 64 * 3
    assert 0 < buf.stored_bytes < raw_bytes, "缓冲里存的应是压缩帧，不是原始数组"


def test_clip_assembler_gathers_tail_before_write(tmp_path):
    """告警后必须真的等后段攒满才落盘——「前 30 秒」不能只有一半。"""
    import cv2

    from fire_sight.alerting.clip_buffer import ClipAssembler

    pre = [np.full((16, 16, 3), 10, dtype=np.uint8) for _ in range(4)]
    asm = ClipAssembler(pre_frames=pre, tail_frames=3, fps=5, out_path=tmp_path / "e1.mp4")
    assert asm.ready is False

    for _ in range(2):
        asm.add(np.full((16, 16, 3), 200, dtype=np.uint8))
    assert asm.ready is False, "尾巴没攒满就写，等于片段只有前段"

    asm.add(np.full((16, 16, 3), 200, dtype=np.uint8))
    assert asm.ready is True

    out = asm.write()
    assert out is not None and out.exists()
    cap = cv2.VideoCapture(str(out))
    got = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        got += 1
    cap.release()
    assert got == 7, "前 4 + 后 3 = 7 帧"


def test_clip_assembler_flush_on_stream_end_truncates(tmp_path):
    """源提前结束时尾巴不够，截到现有长度照写——短片段好过没片段。"""
    import cv2

    from fire_sight.alerting.clip_buffer import ClipAssembler

    pre = [np.full((16, 16, 3), 10, dtype=np.uint8) for _ in range(3)]
    asm = ClipAssembler(pre_frames=pre, tail_frames=10, fps=5, out_path=tmp_path / "e2.mp4")
    asm.add(np.full((16, 16, 3), 200, dtype=np.uint8))
    assert asm.ready is False

    out = asm.write()
    assert out is not None and out.exists()
    cap = cv2.VideoCapture(str(out))
    got = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        got += 1
    cap.release()
    assert got == 4, "3 前 + 1 后（截短）"


def test_event_store_set_clip(tmp_path):
    """事件先入库、片段写完回填——中途断电不丢告警记录。"""
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    db = tmp_path / "events.db"
    ev = AlertEvent(
        event_id="evt-9",
        frame_index=3,
        labels=["fire"],
        conf=0.9,
        location="东门",
        message="检测到明火，位置：东门",
    )
    with EventStore(db) as store:
        store.insert(ev, clip_path="")
        assert store.get_event("evt-9")["clip_path"] == ""
        store.set_clip("evt-9", str(tmp_path / "c.webm"))
        assert store.get_event("evt-9")["clip_path"].endswith("c.webm")


def test_write_clip_rejects_empty_frames(tmp_path):
    from fire_sight.alerting.clip_buffer import write_clip

    with pytest.raises(ValueError):
        write_clip([], tmp_path / "x.mp4")


def test_tts_disabled_or_fallback():
    from fire_sight.alerting import TtsNotifier

    off = TtsNotifier(enabled=False)
    assert off.speak("测试") is False
    # enabled but may lack audio device: should not raise
    on = TtsNotifier(enabled=True)
    assert on.speak("火情确认") in (True, False)
