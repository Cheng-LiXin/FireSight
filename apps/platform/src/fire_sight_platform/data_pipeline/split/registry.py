"""划分策略注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from fire_sight_platform.common.registry_utils import import_submodules

SplitFn = Callable[..., Dict[str, List[str]]]


@dataclass(frozen=True)
class StrategyEntry:
    name: str
    func: SplitFn
    requires_labels: bool


_REGISTRY: Dict[str, StrategyEntry] = {}
_loaded = False


def register_strategy(name: str, requires_labels: bool = False):
    def decorator(func: SplitFn) -> SplitFn:
        _REGISTRY[name] = StrategyEntry(name=name, func=func, requires_labels=requires_labels)
        return func

    return decorator


def _lazy_init() -> None:
    global _loaded
    if _loaded:
        return
    from fire_sight_platform.data_pipeline.split import strategies as _pkg

    import_submodules(_pkg)
    _loaded = True


def get_strategy(name: str) -> StrategyEntry:
    _lazy_init()
    if name not in _REGISTRY:
        raise KeyError(f"未知划分策略: {name}, 可选={list(_REGISTRY)}")
    return _REGISTRY[name]


def list_strategies() -> List[str]:
    _lazy_init()
    return sorted(_REGISTRY)
