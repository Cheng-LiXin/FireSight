"""质检注册表 + 严重度 + 结果类型。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

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


from pathlib import Path  # noqa: E402  (kept near Path usage in dataclass)

CheckFn = Callable[[CheckContext], CheckResult]


@dataclass(frozen=True)
class CheckEntry:
    name: str
    func: CheckFn
    severity_hint: CheckSeverity


_REGISTRY: Dict[str, CheckEntry] = {}
_loaded = False


def check(name: str, severity_hint: CheckSeverity = CheckSeverity.WARNING):
    def decorator(func: CheckFn) -> CheckFn:
        _REGISTRY[name] = CheckEntry(name=name, func=func, severity_hint=severity_hint)
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


def get_check(name: str) -> CheckEntry:
    _lazy_init()
    return _REGISTRY[name]


def list_check_names() -> List[str]:
    _lazy_init()
    return sorted(_REGISTRY)
