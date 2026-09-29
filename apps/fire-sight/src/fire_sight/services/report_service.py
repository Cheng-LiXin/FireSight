"""火情分析报告：统计摘要、Prompt 组装、Markdown 归档。

只喂**统计摘要**给模型，不喂原始事件：800 条事件既贵又会稀释重点，
而且模型会去复述个案而不是给结论。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fire_sight_platform.common.paths import DATA_DIR

# 归档目录。可被环境变量覆盖，便于测试与多实例
REPORTS_DIR: Path = Path(os.environ.get("FIRESIGHT_REPORTS_DIR") or (DATA_DIR / "reports"))

# 每类最多带几条典型事件进上下文
SAMPLES_PER_CLASS = 3
# 机位榜取前几
TOP_LOCATIONS = 5


def _rows_in_range(db_path: Path | str, start: str, end: str) -> List[Dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    from fire_sight.alerting import EventStore

    with EventStore(db_path) as store:
        return store.query_events(start=start, end=end, limit=100000, offset=0)


def aggregate_events(db_path: Path | str, start: str, end: str) -> Dict[str, Any]:
    """时间段内的统计摘要。形状稳定——Prompt 与前端都按它说话。"""
    rows = _rows_in_range(db_path, start, end)

    by_class = {"fire": 0, "smoke": 0}
    by_hour = [0] * 24
    by_loc: Dict[str, int] = {}
    confs: List[float] = []
    fp = 0

    for r in rows:
        labels = [x.strip() for x in str(r.get("labels") or "").split(",") if x.strip()]
        for lab in labels:
            if lab in by_class:
                by_class[lab] += 1
        loc = str(r.get("location") or "").strip() or "未标注位置"
        by_loc[loc] = by_loc.get(loc, 0) + 1
        try:
            by_hour[int(str(r.get("ts"))[11:13])] += 1
        except (ValueError, IndexError):
            pass
        confs.append(float(r.get("conf") or 0.0))
        if int(r.get("is_false_positive") or 0) == 1:
            fp += 1

    samples: List[Dict[str, Any]] = []
    for label in ("fire", "smoke"):
        picked = [r for r in rows if label in str(r.get("labels") or "")]
        picked.sort(key=lambda r: float(r.get("conf") or 0.0), reverse=True)
        for r in picked[:SAMPLES_PER_CLASS]:
            samples.append(
                {
                    "event_id": r.get("event_id"),
                    "label": label,
                    "conf": round(float(r.get("conf") or 0.0), 3),
                    "location": r.get("location") or "",
                    "message": r.get("message") or "",
                }
            )

    return {
        "range": {"start": start, "end": end},
        "total": len(rows),
        "by_class": by_class,
        "by_hour": by_hour,
        "by_location": [
            # 同数并列时按机位名定序：榜单顺序不能依赖入库先后（query 是 ts DESC），
            # 否则同一份数据换个写入顺序就会排出不同的榜。
            {"location": k, "count": v}
            for k, v in sorted(by_loc.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_LOCATIONS]
        ],
        "false_positives": fp,
        "false_positive_rate": round(fp / len(rows), 4) if rows else 0.0,
        "avg_conf": round(sum(confs) / len(confs), 4) if confs else 0.0,
        "samples": samples,
    }


_SYSTEM_PROMPT = """你是消防数据分析师。请基于用户给出的告警统计撰写一份中文报告。

硬性要求：
- 只依据给定数据，不得编造具体数字、时间、机位或案例；数据不足的项写「数据不足」。
- 输出 Markdown，必须包含且仅按顺序使用这五个二级标题：
## 火情态势
## 时段与场所规律
## 风险点
## 防火建议
## 处置优先级
- 「防火建议」按场所分条，每条可执行（做什么、在哪儿、多久一次）。
- 「处置优先级」分高/中/低三档，各附一句理由。
- 不要输出标题以外的开场白与结束语。
"""


def build_prompt(summary: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (system, user)。system 稳定可缓存，user 每次变。"""
    import json

    user = (
        f"时间范围：{summary['range']['start']} ~ {summary['range']['end']}\n"
        f"告警统计 JSON：\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        "请据此撰写报告。"
    )
    return _SYSTEM_PROMPT, user


def make_report_id(now: Optional[datetime] = None) -> str:
    """<YYYYMMDD-HHMMSS>-<8位随机>。文件名即 id，排序即时间序。"""
    import uuid

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _dir(directory: Optional[Path]) -> Path:
    target = Path(directory) if directory is not None else REPORTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_report(
    report_id: str,
    meta: Dict[str, Any],
    markdown: str,
    *,
    directory: Optional[Path] = None,
) -> Path:
    """落盘 .md + .json 两份。JSON 是元数据（时间段、模型、统计快照），供回看列表用。"""
    import json

    base = _dir(directory) / report_id
    md_path = base.with_suffix(".md")
    md_path.write_text(markdown, encoding="utf-8")
    base.with_suffix(".json").write_text(
        json.dumps({**meta, "id": report_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_path


def load_report(report_id: str, *, directory: Optional[Path] = None) -> Dict[str, Any]:
    import json

    base = _dir(directory) / report_id
    md_path = base.with_suffix(".md")
    if not md_path.exists():
        raise FileNotFoundError(f"报告不存在: {report_id}")
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    return {"meta": meta, "markdown": md_path.read_text(encoding="utf-8")}


def list_reports(*, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    import json

    out: List[Dict[str, Any]] = []
    for p in sorted(_dir(directory).glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue  # 单个坏文件不该让列表打不开
    return out
