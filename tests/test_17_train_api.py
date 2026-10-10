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
        "model: yolo11n.pt\nepochs: 2\nbatch: 16\nimgsz: 640\n"
        "data: /x/processed/Demo/data.yaml\n",
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


def test_map_key_strips_ultralytics_suffix():
    """键名里的 "(B)" 是 ultralytics 的标注、随版本可能变，查表前必须剥掉。"""
    from fire_sight_platform.training.curves import map_key

    for raw in ("metrics/mAP50(B)", "metrics/mAP50"):
        assert map_key(raw) == ("metrics", "mAP50")
    assert map_key("train/box_loss") == ("losses", "train_box")
    assert map_key("不认识的列") is None


def test_extract_epoch_metrics_handles_dict_loss_items():
    """回归：ultralytics 的 loss_items 是 dict，不是序列。

    按序列去迭代 dict 拿到的是键名（字符串），float("box_loss") 抛异常，
    被容错的 except 吞掉之后训练损失就静默消失了——实测踩到过。
    """
    from fire_sight_platform.training.curves import extract_epoch_metrics

    class Tensorish:
        def __init__(self, v: float) -> None:
            self.v = v

        def __float__(self) -> float:
            return self.v

    losses, metrics = extract_epoch_metrics(
        {"metrics/mAP50(B)": 0.5, "val/box_loss": 1.2},
        {
            "box_loss": Tensorish(0.87),
            "cls_loss": Tensorish(3.16),
            "dfl_loss": Tensorish(0.74),
        },
    )

    assert losses["train_box"] == pytest.approx(0.87)
    assert losses["train_cls"] == pytest.approx(3.16)
    assert losses["train_dfl"] == pytest.approx(0.74)
    assert losses["val_box"] == pytest.approx(1.2)
    assert metrics["mAP50"] == pytest.approx(0.5)


def test_extract_epoch_metrics_handles_sequence_loss_items():
    """老版本按顺序给序列，也要认。"""
    from fire_sight_platform.training.curves import extract_epoch_metrics

    losses, _ = extract_epoch_metrics({}, [1.0, 2.0, 3.0])
    assert losses == {"train_box": 1.0, "train_cls": 2.0, "train_dfl": 3.0}


def test_extract_epoch_metrics_tolerates_junk():
    """元信息是 ultralytics 的内部结构，读不到就少几个键，不该抛。"""
    from fire_sight_platform.training.curves import extract_epoch_metrics

    losses, metrics = extract_epoch_metrics(
        {"unknown/key": 1.0, "metrics/mAP50(B)": "坏值"}, {"box_loss": "坏值"}
    )
    assert metrics == {}
    assert losses == {}


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


def test_list_runs_carries_train_geometry(client, fake_run):
    """batch / imgsz 要出现在列表里：同一个数据集在不同几何下训出来的结果不可直接比。"""
    run = client.get("/api/train/runs").json()[0]
    assert run["batch"] == 16
    assert run["imgsz"] == 640


def test_geometry_is_none_when_args_yaml_is_missing(client, fake_run, tmp_path):
    """老现场可能没有 args.yaml。缺它不能让整个列表接口挂掉，几何给 null。"""
    from fire_sight.api import deps

    (deps.RUNS_DIR / "training" / fake_run / "args.yaml").unlink()
    run = client.get("/api/train/runs").json()[0]
    assert run["batch"] is None
    assert run["imgsz"] is None
    assert run["dataset"] is None
    assert run["epochs_done"] == 2  # 其余字段照旧可用


def test_geometry_survives_junk_values(client, fake_run):
    """args.yaml 是外部文件，值可能是字符串甚至写坏了。显示成 null，不是 500。"""
    from fire_sight.api import deps

    (deps.RUNS_DIR / "training" / fake_run / "args.yaml").write_text(
        "model: yolo11s.pt\nbatch: '16'\nimgsz: 不是数字\ndata: /x/processed/Demo/data.yaml\n",
        encoding="utf-8",
    )
    run = client.get("/api/train/runs").json()[0]
    assert run["batch"] == 16  # 能转成 int 的字符串照收
    assert run["imgsz"] is None


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


def test_artifact_accepts_every_extension_the_pipeline_calls_an_image(client, fake_run):
    """回归 R8：现场图件用的后缀清单曾比别处少一个 `.bmp`。

    同一份图像后缀在三处各写一遍，其中 `deps.IMAGE_SUFFIXES` 漏了 `.bmp`，
    于是 BMP 图件既不出现在清单里、也取不出来。现在统一引
    `constants.IMAGE_EXTENSIONS`，这条测试钉住「清单里的每一种后缀都能被端到端取到」。
    """
    from fire_sight.api import deps
    from fire_sight_platform.common.constants import IMAGE_EXTENSIONS

    run_root = deps.find_training_run(fake_run).root
    for ext in IMAGE_EXTENSIONS:
        (run_root / f"sample{ext}").write_bytes(b"\x89PNG\r\n\x1a\n")

    names = {a["name"] for a in client.get("/api/train/artifacts", params={"run_id": fake_run}).json()}

    for ext in IMAGE_EXTENSIONS:
        assert f"sample{ext}" in names, ext
        r = client.get("/api/train/artifact", params={"run_id": fake_run, "name": f"sample{ext}"})
        assert r.status_code == 200, ext


def test_runs_empty_when_no_training_dir(tmp_path, monkeypatch):
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "RUNS_DIR", tmp_path / "nothing")
    assert deps.training_runs() == []


# --------------------------------------------------------------------------
# 训练任务的守卫
#
# 真正起一次训练要几十秒且依赖 GPU，不适合放进自动化测试——那些路径靠人工
# 验收（见测试说明）。这里只测不需要起进程就能覆盖的分支。
# --------------------------------------------------------------------------


def test_start_job_rejects_unknown_dataset(client):
    """数据集找不到时不该把进程起起来——那只会白等一次模型加载。"""
    r = client.post("/api/train/jobs", json={"dataset": "根本不存在的数据集", "epochs": 1})
    assert r.status_code == 404


@pytest.mark.parametrize(
    "experiment",
    [
        "../../../../EvilExp",
        "../EvilExp",
        "..\\..\\EvilExp",
        "sub/dir",
        "sub\\dir",
        "C:absolute",
        "/absolute",
    ],
)
def test_start_job_rejects_experiment_paths(client, experiment):
    """回归：experiment 会变成 `runs/training/<name>` 的目录名，不能带路径语义。

    ultralytics 的 increment_path 既不 resolve 也不夹取，实测
    `increment_path(Path('E:/FireSight/runs/training') / '../../../../EvilExp')`
    解析到 `E:\\EvilExp`——整轮训练会落到工作区之外，且归档扫不到。
    这里必须是 422（pydantic 校验拒绝），不能是 201。
    """
    r = client.post(
        "/api/train/jobs",
        json={"dataset": "D-Fire/raw", "epochs": 1, "experiment": experiment},
    )
    assert r.status_code == 422, f"{experiment!r} 竟然被接受了: {r.status_code}"


def test_start_job_accepts_a_plain_experiment_name(client, tmp_path, monkeypatch):
    """正常实验名不受影响——校验不能把「我的实验」这种名字也挡掉。"""
    from fire_sight.api import deps
    import fire_sight_platform.training.service as svc

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")
    # 让显存守卫直接放行，避免测试机不同带来的差异
    monkeypatch.setattr(deps, "vram_limits", lambda *a, **k: {"checked": False})

    assert deps.GPU_LOCK.acquire("infer") is True
    try:
        r = client.post(
            "/api/train/jobs",
            json={"dataset": "任意", "epochs": 1, "experiment": "我的实验"},
        )
    finally:
        deps.GPU_LOCK.release()

    # 校验通过，于是走到抢锁那一步被 409 拦下——关键是不是 422
    assert r.status_code == 409


def test_run_training_refuses_a_traversing_experiment(tmp_path, monkeypatch):
    """service 层再兜一道：训练是几十分钟的活，不能只靠 API 层守规矩。"""
    import fire_sight_platform.training.service as svc
    from fire_sight_platform.runtime_config.base import TrainConfig

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")

    with pytest.raises(ValueError, match="单个目录名"):
        svc._train_fresh(
            TrainConfig(dataset="x", epochs=1, experiment="../../../../EvilExp"),
            None,
            lambda _rid: None,
            lambda _m: None,
        )


def test_archive_refuses_a_traversing_run_id(tmp_path, monkeypatch):
    """归档名同理：`..` 会把 best.pt 写到 models/trained 之外成为孤儿。"""
    from fire_sight_platform.training import archive

    monkeypatch.setattr(archive, "TRAINED_MODELS_DIR", tmp_path / "trained")
    best = tmp_path / "best.pt"
    best.write_text("w", encoding="utf-8")

    with pytest.raises(ValueError, match="单个目录名"):
        archive.archive_best_weight(best, "../../escaped")


def test_unknown_job_is_404(client):
    assert client.get("/api/train/jobs/nope").status_code == 404
    assert client.post("/api/train/jobs/nope/stop").status_code == 404
    assert client.get("/api/train/jobs/nope/stream").status_code == 404


def test_start_job_conflicts_with_held_gpu_lock(client, tmp_path, monkeypatch):
    """显存被占用时应返回 409 并说明占用者，而不是让 CUDA 报错。"""
    from fire_sight.api import deps
    import fire_sight_platform.training.service as svc

    # 让数据集校验通过，把测试推进到抢锁那一步
    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")

    assert deps.GPU_LOCK.acquire("infer") is True
    try:
        r = client.post("/api/train/jobs", json={"dataset": "任意", "epochs": 1})
    finally:
        deps.GPU_LOCK.release()

    assert r.status_code == 409
    assert "infer" in r.json()["detail"]


def test_job_list_empty_initially(client):
    body = client.get("/api/train/jobs").json()
    assert isinstance(body, list)


def test_models_endpoint_reports_a_weight_kept_in_the_repo_root(client, tmp_path, monkeypatch):
    """回归：根目录里的 yolo11n.pt 曾被这条接口标成「需下载」。

    界面按 `present` 挑默认模型，而 `yolo11n` 是标定基准（现役对照模型），
    被标 loss 会让默认值漂到 yolo11s 上——训练预算与对照关系一起变形。
    """
    from fire_sight_platform.training import catalog

    root = tmp_path / "root"
    root.mkdir()
    (root / "yolo11n.pt").write_bytes(b"x" * 4096)
    monkeypatch.setattr(catalog, "ROOT_DIR", root)
    monkeypatch.setattr(catalog, "RUNTIME_ULTRALYTICS_DIR", tmp_path / "runtime")

    body = client.get("/api/train/models").json()
    by_stem = {o["stem"]: o for o in body}

    assert [o["stem"] for o in body] == list(catalog.FAMILIES)
    assert by_stem["yolo11n"]["present"] is True
    assert by_stem["yolo11n"]["name"] == "yolo11n.pt"
    assert by_stem["yolo11n"]["size_mb"] is not None
    assert by_stem["yolo11l"]["present"] is False
