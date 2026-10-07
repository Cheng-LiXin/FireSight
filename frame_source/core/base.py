"""FrameSource 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from frame_source.core.types import Frame


class FrameSource(ABC):
    """统一接口: with source: for frame in source: ..."""

    def __init__(self, source_id: str = "") -> None:
        self.source_id = source_id or self.__class__.__name__
        # 源的标称帧率；拿不到（如图片目录）就是 None，由调用方给兜底值
        self.fps: Optional[float] = None
        # 可选的停止信号（threading.Event 之类，鸭子类型只要有 is_set()）。
        # 由上层通过 with_stop_event() 挂上；为 None 时行为与从前完全一致。
        self._stop_event = None

    def with_stop_event(self, stop_event) -> "FrameSource":
        """挂上停止信号；__iter__ 应在每轮开头调用 self.stopped() 提前收尾。"""
        self._stop_event = stop_event
        return self

    def stopped(self) -> bool:
        """是否已被请求停止。"""
        ev = self._stop_event
        return bool(ev is not None and ev.is_set())

    @abstractmethod
    def __iter__(self) -> Iterator[Frame]:
        raise NotImplementedError

    def __enter__(self) -> "FrameSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """释放资源; 子类按需覆盖。"""

    def read_one(self) -> Optional[Frame]:
        """读取下一帧; 无则 None。"""
        if not hasattr(self, "_iter"):
            self._iter = iter(self)  # type: ignore[attr-defined]
        try:
            return next(self._iter)  # type: ignore[arg-type]
        except StopIteration:
            return None
