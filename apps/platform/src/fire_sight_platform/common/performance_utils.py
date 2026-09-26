"""性能测量工具——@time_it 装饰器。"""

import logging
import time
from functools import wraps
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)


def time_it(
    iterations: int = 1,
    name: Optional[Union[str, Callable[..., str]]] = None,
    logger_instance: logging.Logger | None = None,
):
    """通用执行时间测量装饰器。

    name 可以传:
        - None: 用被装饰函数的 __name__
        - str: 固定显示名
        - callable: 接收被装饰函数实参,返回显示名
    """
    log = logger_instance if logger_instance is not None else logger

    def _format_time_auto_unit(seconds: float) -> str:
        if seconds < 0.001:
            return f"{seconds * 1_000_000:.3f} 微秒"
        elif seconds < 1.0:
            return f"{seconds * 1000:.3f} 毫秒"
        elif seconds < 60:
            return f"{seconds:.2f} 秒"
        elif seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins:.0f} 分钟 {secs:.2f} 秒"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            secs = (seconds % 3600) % 60
            return f"{hours:.0f} 小时 {mins:.0f} 分钟 {secs:.2f} 秒"

    def _resolve_name(func, args, kwargs) -> str:
        if callable(name):
            try:
                return name(*args, **kwargs)
            except Exception:
                log.warning(f"time_it: name() 计算失败, 回退到 {func.__name__}", exc_info=True)
                return func.__name__
        if name is not None:
            return name
        return func.__name__

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            display = _resolve_name(func, args, kwargs)
            start = time.perf_counter()
            result = None
            for _ in range(max(1, iterations)):
                result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            log.info(f"[计时] {display}: {_format_time_auto_unit(elapsed)}")
            return result

        return wrapper

    return decorator
