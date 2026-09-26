"""转换器注册表:格式名 → 转换函数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from fire_sight_platform.common.registry_utils import import_submodules
from fire_sight_platform.common.constants import AnnotationFormat

ConvertFn = Callable[[Path, Path, Path, Dict[str, int]], Tuple[int, int]]


@dataclass(frozen=True)
class ConverterEntry:
    name: str
    func: ConvertFn


_REGISTRY: Dict[str, ConverterEntry] = {}
_loaded = False


def register_converter(name: str):
    def decorator(func: ConvertFn) -> ConvertFn:
        _REGISTRY[name] = ConverterEntry(name=name, func=func)
        return func

    return decorator


def _lazy_init() -> None:
    global _loaded
    if _loaded:
        return
    from fire_sight_platform.data_pipeline.convert import converters as _pkg

    import_submodules(_pkg)
    _loaded = True


def get_converter(name: str) -> ConverterFn:
    _lazy_init()
    if name not in _REGISTRY:
        raise KeyError(f"未知标注格式: {name}, 可选={list(_REGISTRY)}")
    return _REGISTRY[name].func


def list_converters() -> List[str]:
    _lazy_init()
    return sorted(_REGISTRY)
