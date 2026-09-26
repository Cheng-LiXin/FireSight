"""scene 模块测试：多帧确认与告警冷却。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP = ROOT / "apps" / "fire-sight" / "src"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _obs(idx, labels):
    from fire_sight.scene import DetectionLite, FrameObservation

    return FrameObservation(
        frame_index=idx,
        detections=[DetectionLite(name=n, conf=0.8) for n in labels],
    )


def test_multi_frame_confirm_requires_k_hits():
    from fire_sight.scene import MultiFrameConfirm

    mc = MultiFrameConfirm(confirm_frames=3)
    assert mc.update(_obs(0, ["fire"])) == set()
    assert mc.update(_obs(1, ["fire"])) == set()
    assert mc.update(_obs(2, ["fire"])) == {"fire"}

    # 中断则重新计
    mc.update(_obs(3, []))
    assert mc.confirmed_labels() == set()


def test_alert_cooldown():
    from fire_sight.scene import AlertPolicy

    clock = {"t": 100.0}
    policy = AlertPolicy(cooldown_sec=30.0, location="东门", clock=lambda: clock["t"])
    assert policy.should_fire({"smoke"}) is True
    ev = policy.fire({"smoke"}, conf=0.9, frame_index=1)
    assert "烟雾" in ev.message or "smoke" in ev.message
    assert policy.should_fire({"smoke"}) is False
    clock["t"] += 31
    assert policy.should_fire({"smoke"}) is True


def test_two_classes_independent_cooldown():
    from fire_sight.scene import AlertPolicy

    clock = {"t": 0.0}
    policy = AlertPolicy(cooldown_sec=30.0, clock=lambda: clock["t"])
    policy.fire({"fire"}, conf=1.0, frame_index=0)
    assert policy.should_fire({"fire"}) is False
    assert policy.should_fire({"smoke"}) is True
