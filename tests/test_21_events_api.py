"""告警事件：筛选、分页、误报复核、导出 CSV、片段回放。

状态变更（标记误报）在**隔离的临时库**里测——E2E 若去改真实事件库，每跑一次就少一条
待复核，测试不可重复。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TODAY = "2026-09-27"
YESTERDAY = "2026-09-26"


@pytest.fixture()
def seeded_db(tmp_path):
    """7 条事件：跨两天、两类混合、1 条已标误报、1 条片段在事件目录内、1 条片段在目录外。"""
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    db = tmp_path / "data" / "events" / "events.db"
    clips = db.parent / "clips"
    clips.mkdir(parents=True)

    inside = clips / "evt-clip.mp4"
    inside.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # 内容无所谓，只要存在
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    rows = [
        ("evt-a", ["smoke"], 0.51, "东门", "检测到烟雾，位置：东门", str(inside)),
        ("evt-b", ["fire"], 0.93, "仓库", "检测到明火，位置：仓库", str(outside)),
        ("evt-c", ["fire", "smoke"], 0.77, "东门", "检测到明火、烟雾，位置：东门", ""),
        ("evt-d", ["smoke"], 0.42, "西侧", "检测到烟雾，位置：西侧", ""),
        ("evt-e", ["fire"], 0.88, "仓库", "检测到明火，位置：仓库", ""),
        ("evt-f", ["smoke"], 0.66, "北门", "检测到烟雾，位置：北门", ""),
        ("evt-g", ["fire"], 0.35, "南门", "检测到明火，位置：南门", ""),
    ]
    with EventStore(db) as store:
        for eid, labels, conf, loc, msg, clip in rows:
            store.insert(
                AlertEvent(
                    event_id=eid,
                    frame_index=1,
                    labels=list(labels),
                    conf=conf,
                    location=loc,
                    message=msg,
                ),
                clip_path=clip,
            )
        store.mark_false_positive("evt-g")

    # EventStore.insert 的 ts 取 datetime.now()，测试要跨天就必须自己改。
    # 直接改这一列，不绕过表结构。
    con = sqlite3.connect(db)
    con.execute(f"UPDATE alerts SET ts='{YESTERDAY}T10:00:00' WHERE event_id IN ('evt-f','evt-g')")
    for eid in ("evt-a", "evt-b", "evt-c", "evt-d", "evt-e"):
        con.execute(f"UPDATE alerts SET ts='{TODAY}T15:0{ord(eid[-1]) % 10}:00' WHERE event_id=?", (eid,))
    con.commit()
    con.close()
    return db


@pytest.fixture()
def client(monkeypatch, seeded_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api import deps
    from fire_sight.api.app import app

    monkeypatch.setattr(deps, "EVENTS_DB", seeded_db)
    return TestClient(app)


# --------------------------------------------------------------------------
# 列表与筛选
# --------------------------------------------------------------------------


def test_list_returns_items_and_total(client):
    body = client.get("/api/events").json()
    assert body["total"] == 7
    assert len(body["items"]) == 7
    assert body["limit"] == 50 and body["offset"] == 0
    first = body["items"][0]
    assert first["event_id"].startswith("evt-")
    assert isinstance(first["labels"], list), "labels 应已拆成数组"
    assert "clip_path" not in first, "不外泄片段绝对路径"


def test_ordered_newest_first(client):
    ids = [r["event_id"] for r in client.get("/api/events").json()["items"]]
    ts = {r["event_id"]: r["ts"] for r in client.get("/api/events").json()["items"]}
    assert ts[ids[0]] >= ts[ids[-1]]


def test_pagination_does_not_overlap_or_drop(client):
    p1 = client.get("/api/events", params={"limit": 3, "offset": 0}).json()
    p2 = client.get("/api/events", params={"limit": 3, "offset": 3}).json()
    p3 = client.get("/api/events", params={"limit": 3, "offset": 6}).json()
    a = [r["event_id"] for r in p1["items"]]
    b = [r["event_id"] for r in p2["items"]]
    c = [r["event_id"] for r in p3["items"]]
    assert len(a) == 3 and len(b) == 3 and len(c) == 1
    assert not set(a) & set(b) and not set(b) & set(c) and not set(a) & set(c)
    assert len(set(a + b + c)) == 7
    # 每一页的 total 都是筛选下的总数，不是本页条数
    assert p1["total"] == p2["total"] == p3["total"] == 7


def test_filter_by_label(client):
    fire = client.get("/api/events", params={"label": "fire"}).json()
    smoke = client.get("/api/events", params={"label": "smoke"}).json()
    assert fire["total"] == 4  # b, c, e, g
    assert smoke["total"] == 4  # a, c, d, f
    assert all("fire" in r["labels"] for r in fire["items"])
    assert all("smoke" in r["labels"] for r in smoke["items"])


def test_filter_by_status(client):
    pending = client.get("/api/events", params={"status": "pending"}).json()
    fp = client.get("/api/events", params={"status": "false_positive"}).json()
    assert pending["total"] == 6
    assert fp["total"] == 1 and fp["items"][0]["event_id"] == "evt-g"
    assert all(not r["is_false_positive"] for r in pending["items"])


def test_filter_by_keyword_matches_message_location_and_id(client):
    assert client.get("/api/events", params={"keyword": "北门"}).json()["total"] == 1
    assert client.get("/api/events", params={"keyword": "evt-c"}).json()["total"] == 1
    assert client.get("/api/events", params={"keyword": "仓库"}).json()["total"] == 2
    assert client.get("/api/events", params={"keyword": "不存在的东西"}).json()["total"] == 0


def test_filter_by_date_range(client):
    today = client.get("/api/events", params={"start": TODAY, "end": TODAY}).json()
    yest = client.get("/api/events", params={"start": YESTERDAY, "end": YESTERDAY}).json()
    assert today["total"] == 5
    assert yest["total"] == 2
    assert {r["event_id"] for r in yest["items"]} == {"evt-f", "evt-g"}


def test_filters_combine(client):
    body = client.get(
        "/api/events", params={"label": "fire", "status": "pending", "start": TODAY}
    ).json()
    assert {r["event_id"] for r in body["items"]} == {"evt-b", "evt-c", "evt-e"}


def test_empty_db_is_not_an_error(tmp_path, monkeypatch):
    """首次部署事件库不存在——空列表，不是 500。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api import deps
    from fire_sight.api.app import app

    monkeypatch.setattr(deps, "EVENTS_DB", tmp_path / "nope" / "events.db")
    body = TestClient(app).get("/api/events").json()
    assert body["items"] == [] and body["total"] == 0


# --------------------------------------------------------------------------
# 参数校验：写错的筛选值必须报错，不能安静地返回空
# --------------------------------------------------------------------------


def test_unknown_label_is_rejected_not_silently_empty(client):
    r = client.get("/api/events", params={"label": "flame"})
    assert r.status_code == 400
    assert "flame" in r.json()["detail"]


def test_unknown_status_is_rejected(client):
    r = client.get("/api/events", params={"status": "done"})
    assert r.status_code == 400


def test_limit_bounds(client):
    assert client.get("/api/events", params={"limit": 0}).status_code == 422
    assert client.get("/api/events", params={"limit": 999}).status_code == 422
    assert client.get("/api/events", params={"offset": -1}).status_code == 422


# --------------------------------------------------------------------------
# 标记误报
# --------------------------------------------------------------------------


def test_mark_false_positive(client):
    assert client.post("/api/events/evt-a/false-positive").json()["is_false_positive"] is True
    assert client.get("/api/events", params={"status": "false_positive"}).json()["total"] == 2
    assert client.get("/api/events", params={"status": "pending"}).json()["total"] == 5


def test_mark_unknown_event_is_404(client):
    r = client.post("/api/events/没有这个/false-positive")
    assert r.status_code == 404
    assert "没有这个" in r.json()["detail"]


def test_marking_twice_is_idempotent(client):
    """已标误报再标一次不该报错——界面可能重放请求。"""
    assert client.post("/api/events/evt-g/false-positive").status_code == 200
    assert client.get("/api/events", params={"status": "false_positive"}).json()["total"] == 1


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------


def test_export_csv_respects_filters_and_has_bom(client):
    r = client.get("/api/events/export", params={"label": "fire"})
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("\ufeff"), "少了 BOM，Excel 打开中文列会乱码"
    lines = [ln for ln in text.lstrip("\ufeff").splitlines() if ln]
    assert lines[0].startswith("event_id,ts,")
    assert len(lines) == 1 + 4, "表头 + fire 的 4 条"
    assert "检测到明火，位置：仓库" in text


def test_export_has_no_stray_carriage_returns(client):
    """csv 自己输出 \\r\\n，若文本层再做一次换行翻译会变成 \\r\\r\\n。"""
    raw = client.get("/api/events/export").content
    assert b"\r\r\n" not in raw


def test_export_empty_filter_still_has_header(client):
    r = client.get("/api/events/export", params={"keyword": "无匹配"})
    lines = [ln for ln in r.content.decode("utf-8").lstrip("\ufeff").splitlines() if ln]
    assert len(lines) == 1 and lines[0].startswith("event_id")


# --------------------------------------------------------------------------
# 片段
# --------------------------------------------------------------------------


def test_clip_inside_events_dir_is_served(client):
    r = client.get("/api/events/evt-a/clip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.content.startswith(b"\x00\x00\x00\x18ftyp")


def test_clip_outside_events_dir_is_refused(client):
    """clip_path 来自库里的绝对路径。不做归属校验，这个接口就是任意文件读取。"""
    r = client.get("/api/events/evt-b/clip")
    assert r.status_code == 400
    assert "事件目录" in r.json()["detail"]


def test_event_without_clip_is_404(client):
    r = client.get("/api/events/evt-c/clip")
    assert r.status_code == 404
    assert "没有留存片段" in r.json()["detail"]


def test_clip_of_missing_file_is_404(client):
    """库里有路径、磁盘上文件没了——要 404 而不是 500。"""
    from fire_sight.api import deps

    assert client.get("/api/events/evt-a/clip").status_code == 200
    Path(deps.EVENTS_DB).joinpath("..", "clips", "evt-clip.mp4").resolve().unlink()
    r2 = client.get("/api/events/evt-a/clip")
    assert r2.status_code == 404
    assert "不存在" in r2.json()["detail"]


def test_clip_unknown_event_is_404(client):
    assert client.get("/api/events/不存在/clip").status_code == 404


def test_has_clip_reflects_disk_not_the_column(client):
    """库里留着的路径可能已经失效，has_clip 要以磁盘为准。"""
    from fire_sight.api import deps

    Path(deps.EVENTS_DB).parent.joinpath("clips", "evt-clip.mp4").unlink()
    items = {r["event_id"]: r for r in client.get("/api/events").json()["items"]}
    assert items["evt-a"]["has_clip"] is False
