"""alerting 模块测试：事件库、片段缓冲、TTS 降级。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

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


def test_ring_buffer_and_write_clip(tmp_path):
    from fire_sight.alerting import RingClipBuffer
    from fire_sight.alerting.clip_buffer import write_clip

    buf = RingClipBuffer(max_frames=5, fps=10)
    for i in range(8):
        img = np.full((24, 32, 3), i, dtype=np.uint8)
        buf.push(i, float(i), img)
    frames = buf.snapshot()
    assert len(frames) == 5  # 只保留最近
    out = write_clip(frames, tmp_path / "clip.mp4", fps=5)
    assert out.exists() and out.stat().st_size > 0


def test_tts_disabled_or_fallback():
    from fire_sight.alerting import TtsNotifier

    off = TtsNotifier(enabled=False)
    assert off.speak("测试") is False
    # enabled but may lack audio device: should not raise
    on = TtsNotifier(enabled=True)
    assert on.speak("火情确认") in (True, False)
