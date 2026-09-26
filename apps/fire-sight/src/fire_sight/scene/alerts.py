"""告警策略：冷却、消息模板。"""

from __future__ import annotations

import time
from typing import List, Optional, Set

from fire_sight.scene.types import AlertEvent


class AlertPolicy:
    """按类别冷却，避免重复轰鸣。与 IO 无关。"""

    def __init__(
        self,
        cooldown_sec: float = 30.0,
        location: str = "",
        clock=None,
    ) -> None:
        self.cooldown_sec = cooldown_sec
        self.location = location
        self._last: dict[str, float] = {}
        self._clock = clock or time.time
        self._seq = 0

    def should_fire(self, labels: Set[str], now: Optional[float] = None) -> bool:
        now = self._clock() if now is None else now
        for label in labels:
            last = self._last.get(label)
            if last is None or (now - last) >= self.cooldown_sec:
                return True
        return False

    def fire(self, labels: Set[str], conf: float, frame_index: int, now: Optional[float] = None) -> AlertEvent:
        now = self._clock() if now is None else now
        for label in labels:
            self._last[label] = now
        self._seq += 1
        label_str = "、".join(sorted(labels))
        loc = f"，位置：{self.location}" if self.location else ""
        msg = f"检测到{label_str}{loc}"
        return AlertEvent(
            event_id=f"evt-{self._seq}",
            frame_index=frame_index,
            labels=sorted(labels),
            conf=conf,
            location=self.location,
            message=msg,
        )
