"""当前权重的持久化与解析。

「当前权重」是**用户显式指定**的一个归档，与「取最新归档」不是一回事。取最新会让
每一次实验都悄悄变成线上模型——2026-09-27 就真发生过：17:16 那次 30 轮、imgsz=512
的实验（mAP50 0.7432）跑完归档后，把 0.7675 那个从识别页顶掉了，而没有任何提示。

状态文件放在 `models/active.json`（gitignore）：它描述的是**本机哪一份本地归档**
是当前权重，而归档本身是 gitignore 的（5 MB 级权重不入库），
所以把指针提交进仓库只会在别的机器上指向不存在的文件。

解析结果带 source/note，让上层能说清「为什么当前是这一个」——尤其是
「指定的已被删除，暂用最新」这种情况，静默回落会让人以为设置丢了。

文件里除了 `run_id`（用户指定）还记 `last_effective`（上次实际生效的），
用来发现「被新归档顶掉」——只保留退路、不报告切换，等于回到没有这段注释之前。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


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

    switched_from: Optional[str] = None
    """上一次生效、这次不再生效的归档。None 表示没发生过切换。"""

    switched: bool = False
    """本次解析是否相对上一次**自动**换了模型（用户没点过「设为当前」）。

    「没指定过当前权重就用最新」这条退路必须留着——否则第一次训练完页面
    就没模型可用了。但它有个副作用：**每有一个新归档落盘，线上模型就悄悄
    换掉一次**。2026-09-28 就真发生了：`20260928-165715` 一归档就顶掉了
    `20260927-204643`，页面上没有任何提示。
    留一条退路不等于让切换不可见——所以这里把「换过」这个事实带出来。
    """


class ActiveModelStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        """读整个状态文件。缺失/损坏/不是对象都返回空字典——不该因为一个状态
        文件坏了就让模型库页打不开。"""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, payload: Dict[str, Any]) -> None:
        """原子写：先写临时文件再替换。

        解析是读路径也会落盘，`write_text` 那种「先截断后写」在并发读取下会被
        看到半截 JSON——状态文件被读坏的代价是模型库页打不开。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _meaningful(payload: Dict[str, Any]) -> Dict[str, Any]:
        """去掉纯元数据后还剩什么。updated_at 不算「有东西要记」——
        否则清除指定后永远剩一个空壳文件。"""
        return {k: v for k, v in payload.items() if k != "updated_at"}

    def read(self) -> Optional[str]:
        """读用户指定值。字段为空返回 None（= 没指定过）。"""
        value = self._load().get("run_id")
        return str(value) if value else None

    def last_effective(self) -> Optional[str]:
        """上次解析出来、实际生效的归档。

        与 `read()` 分开存，因为它跟「用户指定」是两件事：没指定过时 `run_id`
        是空的，但线上仍然有一个模型在跑，那个才是要跟这次比的基准。
        """
        value = self._load().get("last_effective")
        return str(value) if value else None

    def write(self, run_id: Optional[str]) -> None:
        """写入指定值；None 表示清除（回到取最新）。

        只动 `run_id`，保留 `last_effective`——清除指定不等于「换过模型」，
        把基准一起抹掉会让下一次读取误报成切换。
        """
        payload = self._load()
        if run_id is None:
            # 清除 = 删掉 run_id 这个键，而不是写 null：键在就等于「用户指定过」
            payload.pop("run_id", None)
            if not self._meaningful(payload):
                # 既没指定也没基准：删干净，不留 updated_at 空壳
                self.path.unlink(missing_ok=True)
                return
        else:
            payload["run_id"] = run_id
            # 用户刚做了显式选择，那一刻起「上次生效」＝他选的这个，
            # 由下一次 resolve() 重新落盘，避免这里凭空猜一个值。
            payload.pop("last_effective", None)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save(payload)

    def _record_effective(self, run_id: Optional[str]) -> None:
        """把这次生效的归档记下来，作为下次判断「切换了没」的基准。

        值没变就不写——resolve 是读路径，每次解析都落盘会无谓磨损状态文件。
        """
        payload = self._load()
        current = payload.get("last_effective")
        if (str(current) if current else None) == run_id:
            return
        if run_id is None:
            payload.pop("last_effective", None)
        else:
            payload["last_effective"] = run_id
        if not self._meaningful(payload):
            self.path.unlink(missing_ok=True)
            return
        self._save(payload)

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def resolve(self, available: Sequence[str]) -> ActiveState:
        """在**已知的归档列表**里解析当前权重。

        available 由调用方给出（新→旧），本类不去扫目录——它只管状态，
        目录扫描那一套（两种归档布局）留在 `deps.archived_runs()`。

        解析完会顺手把「这次生效的是谁」记下来，作为下次判断切换的基准。
        """
        pinned = self.read()
        previous = self.last_effective()

        if not available:
            self._record_effective(None)
            return ActiveState(None, pinned, "none", "还没有任何归档训练")

        if pinned is None:
            state = self._with_switch(
                ActiveState(
                    available[0],
                    None,
                    "fallback-newest",
                    "没有指定过当前权重，按最新归档取",
                ),
                previous,
                available,
            )
        elif pinned in available:
            state = ActiveState(pinned, pinned, "pinned")
        else:
            # 指向的归档被删了。回落到最新，但必须说出来——否则用户会以为设置丢了
            state = ActiveState(
                available[0],
                pinned,
                "fallback-missing",
                f"指定的 {pinned} 已不在归档里（可能被删），暂用最新归档",
            )

        self._record_effective(state.run_id)
        return state

    @staticmethod
    def _with_switch(
        state: ActiveState, previous: Optional[str], available: Sequence[str]
    ) -> ActiveState:
        """给「自动切换过」的状态补上说明。

        只在**自动**解析（`fallback-newest`）时判切换：用户点「设为当前」引发的
        变化是他自己干的，不需要再提醒一遍。上一次生效的归档若已不在列表里
        （被删了），那是删除流程的失效回落，由 deletion 那条路径负责说明，
        这里不算「被新归档顶掉」。
        """
        if previous is None or previous == state.run_id or previous not in available:
            return state
        return replace(
            state,
            switched=True,
            switched_from=previous,
            note=f"{state.note}；当前权重已从上一次的 {previous} 自动换成 {state.run_id}",
        )
