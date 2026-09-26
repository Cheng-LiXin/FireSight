"""火灾烟雾场景包：时序确认 + 告警策略。"""

from fire_sight.scene.temporal import MultiFrameConfirm
from fire_sight.scene.alerts import AlertPolicy
from fire_sight.scene.types import AlertEvent, DetectionLite, FrameObservation

__all__ = [
    "MultiFrameConfirm",
    "AlertPolicy",
    "AlertEvent",
    "DetectionLite",
    "FrameObservation",
]
