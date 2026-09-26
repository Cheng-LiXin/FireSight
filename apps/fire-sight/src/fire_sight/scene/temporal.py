"""多帧时序确认：抑制单帧误报(高内聚策略)。"""

from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Set

from fire_sight.scene.types import FrameObservation


class MultiFrameConfirm:
    """连续 K 帧出现同一类目标才判定为真警。

    设计: 只依赖 FrameObservation，不碰模型/IO。
    """

    def __init__(self, confirm_frames: int = 3, classes: Optional[List[str]] = None) -> None:
        if confirm_frames < 1:
            raise ValueError("confirm_frames 必须 >= 1")
        self.confirm_frames = confirm_frames
        self.classes = list(classes) if classes else ["fire", "smoke"]
        self._window: Deque[Set[str]] = deque(maxlen=confirm_frames)

    def reset(self) -> None:
        self._window.clear()

    def update(self, obs: FrameObservation) -> Set[str]:
        """喂入一帧，返回当前已确认的类别集合。"""
        self._window.append(set(obs.labels) & set(self.classes))
        return self.confirmed_labels()

    def confirmed_labels(self) -> Set[str]:
        if len(self._window) < self.confirm_frames:
            return set()
        confirmed = set(self.classes)
        for labels in self._window:
            confirmed &= labels
        return confirmed
