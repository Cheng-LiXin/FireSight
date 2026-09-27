"""当前权重的持久化与解析。

「当前权重」是**用户显式指定**的一个归档，与「取最新归档」不是一回事。取最新会让
每一次实验都悄悄变成线上模型——2026-09-27 就真发生过：17:16 那次 30 轮、imgsz=512
的实验（mAP50 0.7432）跑完归档后，把 0.7675 那个从识别页顶掉了，而没有任何提示。

状态文件放在 `models/active.json`（gitignore）：它描述的是**本机哪一份本地归档**
是当前权重，而归档本身是 gitignore 的（5 MB 级权重不入库），
所以把指针提交进仓库只会在别的机器上指向不存在的文件。

解析结果带 source/note，让上层能说清「为什么当前是这一个」——尤其是
「指定的已被删除，暂用最新」这种情况，静默回落会让人以为设置丢了。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


@dataclass(frozen=True)
class ActiveState:
    """当前权重的解析结果。"""

    run_id: Optional[str]
    """实际生效的归档。None 表示一个归档都没有。"""

    pinned: Optional[str]
    """用户指定过的归档。None 表示从没指定过。"""

    source: str
    """`pinned` / `fallback-newest` / `fallback-missing` / `none`。"""

    note: str = ""
    """给人看的一句解释，界面直接显示。"""


class ActiveModelStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def read(self) -> Optional[str]:
        """读用户指定值。文件缺失/损坏/字段为空都返回 None——不该因为一个状态
        文件坏了就让模型库页打不开。"""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = data.get("run_id") if isinstance(data, dict) else None
        return str(value) if value else None

    def write(self, run_id: Optional[str]) -> None:
        """写入指定值；None 表示清除（回到取最新）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if run_id is None:
            # 清除 = 删文件，而不是写 null：文件在就等于「用户指定过」
            if self.path.exists():
                self.path.unlink()
            return
        payload = {
            "run_id": run_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def resolve(self, available: Sequence[str]) -> ActiveState:
        """在**已知的归档列表**里解析当前权重。

        available 由调用方给出（新→旧），本类不去扫目录——它只管状态，
        目录扫描那一套（两种归档布局）留在 `deps.archived_runs()`。
        """
        pinned = self.read()

        if not available:
            return ActiveState(None, pinned, "none", "还没有任何归档训练")

        if pinned is None:
            return ActiveState(
                available[0], None, "fallback-newest", "没有指定过当前权重，按最新归档取"
            )

        if pinned in available:
            return ActiveState(pinned, pinned, "pinned")

        # 指向的归档被删了。回落到最新，但必须说出来——否则用户会以为设置丢了
        return ActiveState(
            available[0],
            pinned,
            "fallback-missing",
            f"指定的 {pinned} 已不在归档里（可能被删），暂用最新归档",
        )
