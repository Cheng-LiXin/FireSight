"""帧源注册表:类型名 → 工厂。"""

from __future__ import annotations

from typing import Callable, Dict

from frame_source.core.base import FrameSource

Factory = Callable[..., FrameSource]

_REGISTRY: Dict[str, Factory] = {}


def register_source(name: str):
    def decorator(factory: Factory) -> Factory:
        _REGISTRY[name] = factory
        return factory

    return decorator


def get_factory(name: str) -> Factory:
    if name not in _REGISTRY:
        from frame_source import sources as _s  # noqa: F401

        if name not in _REGISTRY:
            raise KeyError(f"未知帧源类型: {name}, 可选={list(_REGISTRY)}")
    return _REGISTRY[name]


def list_sources():
    from frame_source import sources as _s  # noqa: F401

    return sorted(_REGISTRY)
