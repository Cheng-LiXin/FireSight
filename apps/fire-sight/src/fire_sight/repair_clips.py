"""fs-repair-clips: 回填历史告警丢失的片段路径。

**为什么需要它**（一次性数据修复，但脚本长期留在仓库里）

2026-10-07 之前，监控停止时把片段回填（`EventStore.set_clip`）放在 `clip-finalize`
后台线程里做，而 `EventStore` 的 SQLite 连接是推理主线程建的，`check_same_thread`
默认为 True，于是跨线程调用抛 `ProgrammingError`，异常被 `except` 吞掉只剩一行
warning。结果：几十 MB 的 webm 实实在在写到了磁盘，事件表里 `clip_path` 始终为空，
界面永远显示「未留存片段」。修复在 `fire_sight.alerting.store`（改成
`check_same_thread=False` + 锁），但**只保证今后不再丢，已丢的路径不会自愈**。

这个脚本按 `event_id` 去 clips 目录里找同名片段，找得到就回填。
刻意保持三个保守设计：

- **只填空，不覆盖**。已经有路径的告警一律跳过，绝不把已有记录改成别的文件。
- **只认磁盘上真实存在且非空的文件**。宁可少数漏补，也不要给一条告警指向一个
  打不开的路径——那比「未留存」更坏，界面会以为有片子却播不出来。
- **默认 dry-run**，要加 `--apply` 才写库。改生产数据不该是一条顺手敲的命令。

事件 ID 与片段文件同名是既有约定（`service.py` 里 `out_path=clip_dir /
f"{event.event_id}.mp4"`，实际后缀由可用编码决定，可能是 webm）。

用法::

    fs-repair-clips                       # 只报告，不写
    fs-repair-clips --apply               # 真回填
    fs-repair-clips --db path/to.db      # 指定事件库
    fs-repair-clips --clips-dir clips/   # 指定片段目录（默认 events.db 同级的 clips/）
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from fire_sight.alerting import EventStore
from fire_sight.api.deps import EVENTS_DB

# 片段后缀按 clip_buffer._CODECS 的优先级顺序探测：VP9/VP8 → avc1 → AV01。
_EXT_PRIORITY: tuple[str, ...] = (".webm", ".mp4")


def _find_clip(clips_dir: Path, event_id: str) -> Path | None:
    """找 event_id 对应的片段文件；找不到或文件为空返回 None。

    空文件要排除：写盘中途断电会留下 0 字节的残骸，把它记成「已留存」会让界面
    显示一个点开是坏文件的链接。
    """
    for ext in _EXT_PRIORITY:
        candidate = clips_dir / f"{event_id}{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _backup(db_path: Path) -> Path:
    """写库前先复制一份数据库。

    回填本身是保守的（只填空），但它改的是生产账册，而这条批数据丢了不会有第二份
    拷贝。一份备份的成本是一次文件复制，出错时的回退成本是「从备份恢复」——
    这个不对称值得这几 MB。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def repair(db_path: Path | str, clips_dir: Path | str | None = None, apply: bool = False) -> dict:
    """回填历史告警的片段路径。返回统计字典。

    apply=False 时只统计不写库（dry-run）。返回：
    ``{"scanned", "filled", "already", "missing", "failures"}``
    """
    db_path = Path(db_path)
    clips_dir = Path(clips_dir) if clips_dir else db_path.parent / "clips"

    if apply:
        backup = _backup(db_path)
        print(f"[备份] {backup}")

    with EventStore(db_path) as store:
        rows: list[dict] = store.list_events(limit=1_000_000)
        stats = {"scanned": 0, "filled": 0, "already": 0, "missing": 0, "failures": 0}
        for row in rows:
            event_id = row["event_id"]
            existing = (row["clip_path"] or "").strip()
            if existing:
                stats["already"] += 1
                continue
            stats["scanned"] += 1
            clip = _find_clip(clips_dir, event_id)
            if clip is None:
                stats["missing"] += 1
                print(f"[跳过] {event_id}：磁盘上没有对应片段")
                continue
            if apply:
                try:
                    store.set_clip(event_id, str(clip))
                except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整批修复
                    stats["failures"] += 1
                    print(f"[失败] {event_id}：{exc}")
                    continue
            stats["filled"] += 1
            verb = "已回填" if apply else "可回填"
            print(f"[{verb}] {event_id} -> {clip}")
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fs-repair-clips",
        description="回填历史告警丢失的片段路径（默认 dry-run，加 --apply 才写库）",
    )
    ap.add_argument("--db", default=str(EVENTS_DB), help="事件库路径，默认 data/events/events.db")
    ap.add_argument("--clips-dir", default=None, help="片段目录，默认事件库同级的 clips/")
    ap.add_argument("--apply", action="store_true", help="真正写库；不加则只报告")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"事件库不存在: {db_path}", file=sys.stderr)
        return 2

    stats = repair(db_path, args.clips_dir, apply=args.apply)
    mode = "回填" if args.apply else "预演"
    print(
        f"[{mode}完成] 扫描 {stats['scanned']} 条空路径告警，"
        f"命中 {stats['filled']}，无片段 {stats['missing']}，失败 {stats['failures']}；"
        f"已有路径跳过 {stats['already']} 条"
    )
    if not args.apply and stats["filled"]:
        print("加 --apply 才会真正写入数据库。")
    return 1 if stats["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
