"""帧源注册表:类型名 → 工厂。"""

from __future__ import annotations

from typing import Callable, Dict

from frame_source.core.base import FrameSource

Factory = Callable[..., FrameSource]

_REGISTRY: Dict[str, Factory] = {}


def register_source(name: str):
    def decorator(factory: Factory) -> Factory:
        if name in _REGISTRY:
            # 静默覆盖会让两个模块抢同一个名字、谁赢取决于导入顺序。
            raise ValueError(f"帧源类型重复注册: {name}")
        _REGISTRY[name] = factory
        return factory

    return decorator


def get_factory(name: str) -> Factory:
    if name not in _REGISTRY:
        from frame_source import sources as _s  # noqa: F401

        if name not in _REGISTRY:
            # 与 factory.py 的「无法识别帧源」保持同一种异常：调用方只需接
            # 一个 ValueError，不必区分「类型名写错」和「路径识别不出」。
            raise ValueError(f"未知帧源类型: {name}, 可选={list(_REGISTRY)}")
    return _REGISTRY[name]


def list_sources():
    from frame_source import sources as _s  # noqa: F401

    return sorted(_REGISTRY)
