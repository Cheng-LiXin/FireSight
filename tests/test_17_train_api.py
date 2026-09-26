"""训练中心：曲线解析与只读接口。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

HEADER = (
    "epoch,time,train/box_loss,train/cls_loss,train/dfl_loss,"
    "metrics/precision(B),metrics/recall(B),metrics/mAP50(B),metrics/mAP50-95(B),"
    "val/box_loss,val/cls_loss,val/dfl_loss,lr/pg0,lr/pg1,lr/pg2"
)


def _write_csv(tmp_path: Path, rows: list[str], header: str = HEADER) -> Path:
    path = tmp_path / "results.csv"
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_cache():
    from fire_sight.api import deps

    deps.clear_infer_cache()
    yield


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    return TestClient(app)


@pytest.fixture()
def fake_run(tmp_path, monkeypatch):
    """一次最小可用的训练现场：results.csv + 一张图件 + args.yaml。"""
    from fire_sight.api import deps

    run_root = tmp_path / "runs" / "training" / "20260101-000000-Demo" / "train-yolo11n"
    run_root.mkdir(parents=True)
    _write_csv(
        run_root,
        [
            "1,10,1.5,2.0,1.1,0.40,0.35,0.30,0.10,1.6,2.1,1.2,0.01,0.01,0.01",
            "2,20,1.2,1.7,0.9,0.55,0.48,0.42,0.18,1.4,1.9,1.1,0.01,0.01,0.01",
        ],
    )
    (run_root / "confusion_matrix.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run_root / "args.yaml").write_text(
        "model: yolo11n.pt\nepochs: 2\ndata: /x/processed/Demo/data.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deps, "RUNS_DIR", tmp_path / "runs")
    return "20260101-000000-Demo/train-yolo11n"


# --------------------------------------------------------------------------
# 曲线解析（纯函数）
# --------------------------------------------------------------------------


def test_reads_all_series(tmp_path):
    from fire_sight_platform.training.curves import read_results_csv

    c = read_results_csv(_write_csv(tmp_path, [
        "1,10,1.5,2.0,1.1,0.40,0.35,0.30,0.10,1.6,2.1,1.2,0.01,0.01,0.01",
        "2,20,1.2,1.7,0.9,0.55,0.48,0.42,0.18,1.4,1.9,1.1,0.01,0.01,0.01",
    ]))

    assert c.epochs == [1, 2]
    assert c.total_epochs == 2
    assert c.metrics["mAP50"] == [0.30, 0.42]
    assert c.metrics["mAP50_95"] == [0.10, 0.18]
    assert c.losses["train_box"] == [1.5, 1.2]
    assert c.losses["val_dfl"] == [1.2, 1.1]
    assert set(c.lr) == {"pg0", "pg1", "pg2"}


def test_all_series_are_equal_length(tmp_path):
    """各序列必须等长，否则前端按 epoch 索引会错位。"""
    from fire_sight_platform.training.curves import read_results_csv

    c = read_results_csv(_write_csv(tmp_path, [
        "1,10,1.5,2.0,1.1,0.40,0.35,0.30,0.10,1.6,2.1,1.2,0.01,0.01,0.01",
        "2,20,1.2,1.7,0.9,0.55,0.48,0.42,0.18,1.4,1.9,1.1,0.01,0.01,0.01",
        "3,30,1.0,1.4,0.8,0.60,0.52,0.50,0.22,1.3,1.7,1.0,0.01,0.01,0.01",
    ]))
    for series in list(c.metrics.values()) + list(c.losses.values()) + list(c.lr.values()):
        assert len(series) == c.total_epochs


def test_unparsable_value_becomes_none_not_zero(tmp_path):
    """缺值必须是 None。填 0 会让折线砸到谷底，读起来像「指标归零」。"""
    from fire_sight_platform.training.curves import read_results_csv

    c = read_results_csv(_write_csv(tmp_path, [
        "1,10,1.5,2.0,1.1,0.40,0.35,0.30,0.10,1.6,2.1,1.2,0.01,0.01,0.01",
        "2,20,1.2,1.7,0.9,,,,,1.4,1.9,1.1,0.01,0.01,0.01",
    ]))

    assert c.metrics["mAP50"] == [0.30, None]
    assert len(c.metrics["mAP50"]) == 2


def test_missing_file_returns_empty(tmp_path):
    from fire_sight_platform.training.curves import read_results_csv

    c = read_results_csv(tmp_path / "nope.csv")
    assert c.empty
    assert c.total_epochs == 0


def test_csv_without_epoch_column_is_rejected(tmp_path):
    from fire_sight_platform.training.curves import read_results_csv

    path = _write_csv(tmp_path, ["1.5,2.0"], header="a,b")
    assert read_results_csv(path).empty


def test_unknown_columns_are_ignored(tmp_path):
    """ultralytics 换版本可能加列，不该因此整份解析失败。"""
    from fire_sight_platform.training.curves import read_results_csv

    header = "epoch,train/box_loss,some/new_metric,metrics/mAP50(B)"
    c = read_results_csv(_write_csv(tmp_path, ["1,1.5,9.9,0.30"], header=header))
    assert c.epochs == [1]
    assert c.metrics["mAP50"] == [0.30]
    assert c.losses["train_box"] == [1.5]


# --------------------------------------------------------------------------
# 接口
# --------------------------------------------------------------------------


def test_list_runs(client, fake_run):
    body = client.get("/api/train/runs").json()
    assert len(body) == 1
    run = body[0]
    assert run["run_id"] == fake_run
    assert run["epochs_done"] == 2
    assert run["dataset"] == "Demo"
    assert run["model"] == "yolo11n.pt"
    assert run["archived"] is False
    assert run["mAP50"] is None  # 未归档，没有指标
    assert run["artifact_count"] == 1


def test_curves_endpoint(client, fake_run):
    body = client.get("/api/train/curves", params={"run_id": fake_run}).json()
    assert body["total_epochs"] == 2
    assert body["metrics"]["mAP50"] == [0.30, 0.42]


def test_curves_unknown_run_is_404(client, fake_run):
    r = client.get("/api/train/curves", params={"run_id": "nope"})
    assert r.status_code == 404


def test_artifact_list_and_fetch(client, fake_run):
    items = client.get("/api/train/artifacts", params={"run_id": fake_run}).json()
    assert [a["name"] for a in items] == ["confusion_matrix.png"]
    assert items[0]["kind"] == "confusion"

    r = client.get(
        "/api/train/artifact", params={"run_id": fake_run, "name": "confusion_matrix.png"}
    )
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_artifact_rejects_path_traversal(client, fake_run):
    """图件名只允许现场目录下的顶层文件。"""
    for bad in ("../args.yaml", "..\\args.yaml", "sub/confusion_matrix.png"):
        r = client.get("/api/train/artifact", params={"run_id": fake_run, "name": bad})
        assert r.status_code in (400, 404), bad


def test_artifact_rejects_non_image(client, fake_run):
    r = client.get("/api/train/artifact", params={"run_id": fake_run, "name": "args.yaml"})
    assert r.status_code == 404


def test_runs_empty_when_no_training_dir(tmp_path, monkeypatch):
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "RUNS_DIR", tmp_path / "nothing")
    assert deps.training_runs() == []
