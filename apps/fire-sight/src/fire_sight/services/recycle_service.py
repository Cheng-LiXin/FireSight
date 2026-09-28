"""归档与训练现场的「删除」——实际是移进回收目录。

为什么不是真删
--------------
权重是几十分钟到两小时的产物：本机 yolo11s/640px/50 轮 = 约 140 分钟。误点一次
就永久消失的代价太大，所以删除一律**移到 `models/.trash/`**：页面上不再出现、
磁盘不立即释放，换来「删错了能捞回来」。想真释放空间由用户手工清空那个目录——
这个决定故意不自动化，自动清理等于把回收目录变回真删除。

删到什么程度
------------
归档（`models/trained/<run_id>`）与训练现场（`runs/training/<run_id>`）是**两份**
数据，前者是能部署的权重，后者是曲线、图件、检查点。它们可能同名，也可能只有一边
存在（比如被强行停止、还没归档的训练，只有现场）。所以删除接口分别接受两个开关，
由用户逐次决定，而不是替他把两边绑死。

两处易错点
----------
1. **run_id 可能含斜杠**（早期布局 `20260926-190635-D-Fire/train-yolo11n`），所以
   目标路径必须由**已发现的 run** 反查得到，绝不拿用户字符串拼路径——否则这个接口
   就是一个能删工作区任意文件的后门。
2. **删的如果是当前权重**，必须清推理缓存并把状态文件一起处理，否则识别页还拿着
   内存里那份已经不存在的权重继续推理，直到进程重启。回落由 `ActiveModelStore`
   自己表达成 `fallback-missing` 并给出说明，不静默换成别的模型。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fire_sight_platform.common.paths import TRAINED_MODELS_DIR, TRASH_DIR


@dataclass
class Removed:
    """一次删除实际搬走的东西。"""

    #: 搬到了哪里（回收目录下的时间戳子目录）
    trash_dir: Path
    #: 相对路径清单，供界面如实回显「删了什么」
    items: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)


def _unique_dir(stamp: str) -> Path:
    """同一秒内删两次也要落在不同目录，否则第二次会覆盖第一次。"""
    base = TRASH_DIR / stamp
    if not base.exists():
        return base
    n = 2
    while (TRASH_DIR / f"{stamp}-{n}").exists():
        n += 1
    return TRASH_DIR / f"{stamp}-{n}"


def _move(src: Path, trash_dir: Path, label: str) -> bool:
    """把 src 搬进 trash_dir，返回是否真的搬了。

    用 `shutil.move` 而不是 `rename`：后者在跨盘（工作区与 .runtime 不在同一卷）时
    会 OSError，而这两个目录都在工作区内、通常同卷，但我不想把正确性押在这上面。
    """
    if not src.exists():
        return False
    target = trash_dir / label
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    return True


def _cleanup_empty_parents(path: Path, stop: Path) -> None:
    """删掉归档后可能留下的空壳目录，且**不能越过 stop**。

    嵌套布局 `trained/<run_id>/<experiment>/best.pt` 搬走 experiment 之后，
    `trained/<run_id>/` 就成了空目录；它会让 `archived_runs()` 多扫一层、
    也在文件管理器里留个空文件夹。只往上清到 stop 为止，绝不删 stop 本身。

    用 `is_relative_to` 而不是 `stop in current.parents`：前者同时覆盖
    「path 就在 stop 底下」和「path == stop」（后者直接不动手）。

    边界必须由调用方**显式传入**，不能在本模块里靠路径形状反推——归档有两种
    布局，而嵌套布局里 `<exp>/` 自己也有 best.pt，「看自己有没有 best.pt」
    区分不出浅层与嵌套（两种情况下都会命中同一个答案，等于没判断）。
    归档根由扫描归档的 `deps` 侧最清楚，就由它给。
    """
    current = path
    while current != stop and current.is_relative_to(stop):
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def recycle(
    run_id: str,
    archive_root: Optional[Path] = None,
    run_root: Optional[Path] = None,
    archive_base: Optional[Path] = None,
    when: Optional[datetime] = None,
) -> Removed:
    """把归档与/或训练现场移进回收目录。

    两个 root 都是**已经由调用方校验过的真实目录**（来自 `deps.archived_runs()`
    或 `deps.training_runs()` 的扫描结果），本函数不再做路径合法性判断——
    校验与执行分开，是为了让「能删什么」这件事只有一个定义处。

    Args:
        run_id: 仅用于给回收目录起名，便于事后认出这是哪一次。
        archive_root: 归档目录（含 best.pt）；None 表示不动归档。
        run_root: 训练现场目录（含 results.csv）；None 表示不动现场。
        archive_base: 归档根目录，清空壳时不许越过的那一层。None 表示不清空壳。
        when: 注入时间便于测试；默认取当前时间。
    """
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    # 回收目录名里带上原 run_id（斜杠换成下划线），事后一眼能看出是哪次实验
    slug = run_id.replace("/", "_").replace("\\", "_")
    trash_dir = _unique_dir(f"{stamp}-{slug}")

    removed = Removed(trash_dir=trash_dir)

    if archive_root is not None and _move(archive_root, trash_dir, "archive"):
        removed.items.append(str(archive_root))
        if archive_base is not None:
            _cleanup_empty_parents(archive_root.parent, archive_base)

    if run_root is not None and _move(run_root, trash_dir, "run"):
        removed.items.append(str(run_root))

    # 一件都没搬走就别留下空目录——否则回收站会堆一串空壳，反而看不出删过什么
    if not removed.items and trash_dir.exists():
        try:
            trash_dir.rmdir()
        except OSError:
            pass

    return removed
