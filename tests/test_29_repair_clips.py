"""历史告警片段路径回填脚本的回归测试。

要守的是脚本的三个保守设计（都写在 `repair_clips` 的模块 docstring 里）：
只填空不覆盖、只认真实非空的片段、默认 dry-run。生产账册改坏了没法重来，
所以这些性质要有测试钉住。
"""

from __future__ import annotations

from pathlib import Path

from fire_sight.alerting import EventStore
from fire_sight.repair_clips import main as repair_main
from fire_sight.repair_clips import repair
from fire_sight.scene.types import AlertEvent


def _event(event_id: str) -> AlertEvent:
    return AlertEvent(
        event_id=event_id,
        frame_index=3,
        labels=["fire"],
        conf=0.9,
        location="东门",
        message=f"检测到明火：{event_id}",
    )


def _seed(db: Path, entries: list[tuple[str, str]]) -> None:
    """写入若干告警，entries 是 (event_id, clip_path)。"""
    with EventStore(db) as store:
        for event_id, clip in entries:
            store.insert(_event(event_id), clip_path=clip)


def _clip(clips_dir: Path, name: str, size: int = 2048) -> Path:
    clips_dir.mkdir(parents=True, exist_ok=True)
    path = clips_dir / name
    path.write_bytes(b"\x00" * size)
    return path


def test_dry_run_reports_but_never_writes(tmp_path):
    db = tmp_path / "events.db"
    clips = tmp_path / "clips"
    _seed(db, [("evt-a", ""), ("evt-b", "")])
    _clip(clips, "evt-a.webm")
    _clip(clips, "evt-b.webm")

    stats = repair(db, clips, apply=False)
    assert stats == {
        "scanned": 2,
        "filled": 2,
        "already": 0,
        "missing": 0,
        "failures": 0,
    }

    # 预演只是「查到了」，库必须原封不动
    with EventStore(db) as store:
        assert store.get_event("evt-a")["clip_path"] == ""
        assert store.get_event("evt-b")["clip_path"] == ""


def test_apply_fills_paths_and_never_overwrites_existing(tmp_path):
    db = tmp_path / "events.db"
    clips = tmp_path / "clips"
    _seed(db, [("evt-a", ""), ("evt-keep", "E:/旧路径/evt-keep.webm")])
    _clip(clips, "evt-a.webm")
    _clip(clips, "evt-keep.webm")

    stats = repair(db, clips, apply=True)
    assert stats["filled"] == 1
    assert stats["already"] == 1

    with EventStore(db) as store:
        assert store.get_event("evt-a")["clip_path"] == str(clips / "evt-a.webm")
        # 已有路径必须保持原值——覆盖它等于毁掉一份有效记录
        assert store.get_event("evt-keep")["clip_path"] == "E:/旧路径/evt-keep.webm"


def test_empty_or_missing_clip_is_left_unfilled(tmp_path):
    """0 字节的残骸不算「已留存」。

    写盘中途断电会留下 0 字节文件，把它记成有效路径，界面就会显示一个点开是
    坏文件的链接——比老老实实显示「未留存」更坏。
    """
    db = tmp_path / "events.db"
    clips = tmp_path / "clips"
    _seed(db, [("evt-zero", ""), ("evt-gone", "")])
    _clip(clips, "evt-zero.webm", size=0)
    # evt-gone 磁盘上根本没有

    stats = repair(db, clips, apply=True)
    assert stats["filled"] == 0
    assert stats["missing"] == 2

    with EventStore(db) as store:
        assert store.get_event("evt-zero")["clip_path"] == ""
        assert store.get_event("evt-gone")["clip_path"] == ""


def test_mp4_clip_is_found_when_webm_is_absent(tmp_path):
    """片段后缀由可用编码决定，可能是 mp4——只认 webm 会漏掉它们。"""
    db = tmp_path / "events.db"
    clips = tmp_path / "clips"
    _seed(db, [("evt-mp4", "")])
    _clip(clips, "evt-mp4.mp4")

    stats = repair(db, clips, apply=True)
    assert stats["filled"] == 1
    with EventStore(db) as store:
        assert store.get_event("evt-mp4")["clip_path"] == str(clips / "evt-mp4.mp4")


def test_apply_backs_up_the_database(tmp_path):
    db = tmp_path / "events.db"
    clips = tmp_path / "clips"
    _seed(db, [("evt-a", "")])
    _clip(clips, "evt-a.webm")

    repair(db, clips, apply=True)

    backups = list(tmp_path.glob("events.db.bak-*"))
    assert len(backups) == 1, "写库前应先复制一份，出错时才有得回退"
    assert backups[0].stat().st_size > 0


def test_cli_defaults_to_dry_run_and_reports(tmp_path, capsys):
    db = tmp_path / "events.db"
    clips = tmp_path / "clips"
    _seed(db, [("evt-a", "")])
    _clip(clips, "evt-a.webm")

    assert repair_main(["--db", str(db), "--clips-dir", str(clips)]) == 0
    out = capsys.readouterr().out
    assert "[可回填] evt-a" in out
    assert "加 --apply 才会真正写入数据库。" in out

    with EventStore(db) as store:
        assert store.get_event("evt-a")["clip_path"] == ""


def test_cli_refuses_a_missing_database(tmp_path):
    assert repair_main(["--db", str(tmp_path / "nope.db")]) == 2
