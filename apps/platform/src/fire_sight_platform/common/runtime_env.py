"""把会外溢到系统盘的缓存与临时目录改道进 RUNTIME_DIR。

**必须在 import ultralytics / torch / matplotlib 之前调用。** ultralytics 在
import 时就把 settings.json 里的 runs_dir / weights_dir / datasets_dir 读成
模块常量(`ultralytics.utils.RUNS_DIR` 等),之后再用 `SETTINGS.update()` 只能
改动读字典的那部分调用,改不动这些常量。所以改道靠环境变量抢在它前面:

    YOLO_CONFIG_DIR   决定 ultralytics 读哪一份 settings.json
    TEMP/TMP/TMPDIR   子进程与各类库的临时目录
    MPLCONFIGDIR      ultralytics 画图用的 matplotlib 字体缓存
    TORCH_HOME/HF_HOME/XDG_CACHE_HOME  权重与模型缓存

本机实测:改道之前 `%APPDATA%\\Ultralytics\\settings.json` 指向
`C:\\Users\\19832\\runs`,于是 `fs-val` 的 `model.val()` 把 24 个图件写进了
C 盘(`runs/detect/val`、`val-2`);AMP 自检还把 `yolo26n.pt` 下到了
`C:\\Users\\19832\\weights`。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fire_sight_platform.common.paths import (
    RUNTIME_CACHE_DIR,
    RUNTIME_DIR,
    RUNTIME_TMP_DIR,
    RUNTIME_ULTRALYTICS_DIR,
)

logger = logging.getLogger(__name__)

# ultralytics 的三处全局目录。分开三份而不是共用 RUNTIME_DIR:它自带一条校验,
# datasets_dir 与 runs_dir 相同时会告警。
def ultralytics_dirs() -> dict[str, Path]:
    """ultralytics 三个全局目录的落点。

    每次调用现算,不缓存成模块常量——缓存会让「换 RUNTIME_DIR 就换落点」这条
    性质在运行期失效,测试里也改不动。
    """
    return {
        "datasets_dir": RUNTIME_ULTRALYTICS_DIR / "datasets",
        "weights_dir": RUNTIME_ULTRALYTICS_DIR / "weights",
        "runs_dir": RUNTIME_ULTRALYTICS_DIR / "runs",
    }


_CACHE_DIRS = (
    RUNTIME_CACHE_DIR / "matplotlib",
    RUNTIME_CACHE_DIR / "torch",
    RUNTIME_CACHE_DIR / "huggingface",
)


def runtime_env() -> dict[str, str]:
    """改道后的环境变量映射(纯函数,不碰磁盘也不碰 os.environ)。"""
    return {
        "YOLO_CONFIG_DIR": str(RUNTIME_ULTRALYTICS_DIR),
        "TEMP": str(RUNTIME_TMP_DIR),
        "TMP": str(RUNTIME_TMP_DIR),
        "TMPDIR": str(RUNTIME_TMP_DIR),
        "MPLCONFIGDIR": str(RUNTIME_CACHE_DIR / "matplotlib"),
        "TORCH_HOME": str(RUNTIME_CACHE_DIR / "torch"),
        "HF_HOME": str(RUNTIME_CACHE_DIR / "huggingface"),
        "XDG_CACHE_HOME": str(RUNTIME_CACHE_DIR),
    }


def ensure_dirs() -> None:
    """建好运行时目录。幂等,失败就让异常冒出去——写不进工作区是部署问题,
    不该拖着等训练跑到一半才炸。"""
    for path in (
        RUNTIME_DIR,
        RUNTIME_TMP_DIR,
        RUNTIME_CACHE_DIR,
        RUNTIME_ULTRALYTICS_DIR,
        *_CACHE_DIRS,
        *ultralytics_dirs().values(),
    ):
        path.mkdir(parents=True, exist_ok=True)


def apply_runtime_env() -> dict[str, str]:
    """建目录并写入本进程环境。幂等,可重复调用。"""
    ensure_dirs()
    mapping = runtime_env()
    os.environ.update(mapping)
    return mapping


def pin_ultralytics_paths() -> bool:
    """ultralytics 已导入后,把三处全局目录钉进它当前那份 settings.json。

    本进程内 `get_save_dir()` 读的是 `SETTINGS` 这份 dict,所以立刻生效;对
    后续进程则靠落盘的 settings.json 生效——两者都要,缺一个就会漏。

    只写 `YOLO_CONFIG_DIR` 底下的那份配置。若当前生效的仍是用户的全局配置
    (%APPDATA%),直接拒绝并告警:那是人家机器级的东西,项目不该偷改。
    返回是否写成功。
    """
    apply_runtime_env()

    try:
        from ultralytics.utils import SETTINGS, USER_CONFIG_DIR
    except Exception as exc:  # noqa: BLE001 - 拿不到就跳过,不该因此中断训练
        logger.warning(f"未能读取 ultralytics 配置,跳过目录钉定: {exc}")
        return False

    expected = RUNTIME_ULTRALYTICS_DIR.resolve()
    actual = Path(USER_CONFIG_DIR).resolve()
    if actual != expected and expected not in actual.parents:
        logger.warning(
            f"ultralytics 配置目录 {actual} 不在运行时目录下,拒绝改写。"
            f"请确认 YOLO_CONFIG_DIR 已在本进程首次 import ultralytics 前设好。"
        )
        return False

    try:
        SETTINGS.update({k: str(v) for k, v in ultralytics_dirs().items()})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"钉定 ultralytics 目录失败: {exc}")
        return False
    return True
