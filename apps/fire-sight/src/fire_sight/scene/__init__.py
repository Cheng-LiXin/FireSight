"""火灾烟雾场景包：时序确认、告警策略、类别映射。"""

from fire_sight.scene.alerts import AlertPolicy
from fire_sight.scene.class_map import (
    CLASS_NAMES,
    MODEL_ID_TO_NAME,
    NAME_CN,
    class_to_id,
    display_label,
    display_name,
    names_dict,
)
from fire_sight.scene.temporal import MultiFrameConfirm
from fire_sight.scene.types import AlertEvent, DetectionLite, FrameObservation

__all__ = [
    "AlertPolicy",
    "CLASS_NAMES",
    "MODEL_ID_TO_NAME",
    "NAME_CN",
    "MultiFrameConfirm",
    "AlertEvent",
    "DetectionLite",
    "FrameObservation",
    "class_to_id",
    "display_label",
    "display_name",
    "names_dict",
]
