"""质检注册表 + 严重度 + 结果类型。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List

from fire_sight_platform.common.registry_utils import import_submodules

logger = logging.getLogger(__name__)


class CheckSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    PASS = "PASS"


@dataclass
class CheckResult:
    name: str
    severity: CheckSeverity
    message: str
    details: List[str] = field(default_factory=list)


@dataclass
class CheckContext:
    yaml_path: Path
    data_root: Path
    names: Dict[int, str]
    # filled by snapshot
    splits: Dict[str, List[Path]] = field(default_factory=dict)
    label_files: Dict[str, List[Path]] = field(default_factory=dict)
    # 每个划分实际用的标签目录。snapshot 已解析好两种布局（processed 的
    # `labels/<split>` 与提供方划分的 `<split>/labels`），检查项不要再自己拼。
    label_dirs: Dict[str, Path] = field(default_factory=dict)


from pathlib import Path  # noqa: E402  (kept near Path usage in dataclass)

CheckFn = Callable[[CheckContext], CheckResult]


@dataclass(frozen=True)
class CheckEntry:
    name: str
    func: CheckFn


_REGISTRY: Dict[str, CheckEntry] = {}
_loaded = False


def check(name: str):
    def decorator(func: CheckFn) -> CheckFn:
        _REGISTRY[name] = CheckEntry(name=name, func=func)
        return func

    return decorator


def _lazy_init() -> None:
    global _loaded
    if _loaded:
        return
    from fire_sight_platform.data_validation import checks as _pkg

    import_submodules(_pkg)
    _loaded = True


def get_all_checks() -> List[CheckEntry]:
    _lazy_init()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
