"""注册表公共件:子模块自动发现与导入。"""

import importlib
import pkgutil
from types import ModuleType


def import_submodules(package: ModuleType) -> None:
    """导入 package 下所有子模块(触发注册器副作用)。"""
    if not hasattr(package, "__path__"):
        return
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{package.__name__}.{info.name}")
