"""场景层共享类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DetectionLite:
    """场景层只关心类别与置信度(与可视化/模型解耦)。"""

    name: str
    conf: float
    box: Optional[tuple] = None


@dataclass
class FrameObservation:
    """单帧观测结果。"""

    frame_index: int
    detections: List[DetectionLite] = field(default_factory=list)

    @property
    def labels(self) -> set:
        return {d.name for d in self.detections}

    def max_conf(self, name: str) -> float:
        vals = [d.conf for d in self.detections if d.name == name]
        return max(vals) if vals else 0.0


@dataclass
class AlertEvent:
    """一次确认后的告警。"""

    event_id: str
    frame_index: int
    labels: List[str]
    conf: float
    location: str = ""
    message: str = ""
