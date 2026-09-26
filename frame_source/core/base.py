"""FrameSource 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from frame_source.core.types import Frame


class FrameSource(ABC):
    """统一接口: with source: for frame in source: ..."""

    def __init__(self, source_id: str = "") -> None:
        self.source_id = source_id or self.__class__.__name__

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
