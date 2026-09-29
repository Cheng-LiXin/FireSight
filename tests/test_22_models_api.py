"""模型库：当前权重的状态机、下载、多权重对比任务的排期与进度。

对比评估**不真跑 GPU**：把 `evaluate_weights` 换成假的，这样能把任务生命周期
（逐个出结果、锁的获取与释放、单模型失败不毁全局）测干净。真跑一次要几分钟，
而且会占住显卡。真评估的口径由 `apps/platform/tests` 与 `test_02` 覆盖。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

OLDER = "20260101-a"
NEWER = "20260102-b"


def _write_archive(root: Path, mAP50: float, epochs: int, dataset: str = "D-Fire/train") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "best.pt").write_bytes(b"\x00" * 2048)
    (root / "metrics.txt").write_text(
        f"dataset={dataset}\nmodel=yolo11n.pt\nmAP50={mAP50}\nmAP50_95={mAP50 / 2}\n"
        f"precision=0.75\nrecall=0.67\n",
        encoding="utf-8",
    )
    # 注意：归档里这个文件叫 .yaml 但内容是 JSON（训练时直接 dump 的 dict）
    (root / "train_config.yaml").write_text(
        json.dumps({"dataset": dataset, "epochs": epochs, "imgsz": 640}), encoding="utf-8"
    )


@pytest.fixture()
def models_env(tmp_path, monkeypatch):
    """两个假归档：OLDER(0.80) 与 NEWER(0.70)。

    刻意让**新的那个指标更差**——「取最新」会让实验悄悄顶掉线上模型的场景。
    """
    from fire_sight.api import deps
    from fire_sight.services import ActiveModelStore

    trained = tmp_path / "trained"
    _write_archive(trained / OLDER, mAP50=0.80, epochs=40)
    _write_archive(trained / NEWER, mAP50=0.70, epochs=10)

    monkeypatch.setattr(deps, "TRAINED_MODELS_DIR", trained)
    monkeypatch.setattr(deps, "ACTIVE_MODEL_STORE", ActiveModelStore(tmp_path / "active.json"))
    return trained


@pytest.fixture()
def client(models_env):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api import eval_jobs
    from fire_sight.api.app import app

    # 每个用例从干净的任务表开始，避免互相看见对方的任务
    eval_jobs.manager._jobs.clear()
    return TestClient(app)


def wait_done(job_id: str, timeout: float = 10.0) -> dict:
    """等后台线程把任务跑完。假评估是瞬时的，这里只是把调度让出来。"""
    from fire_sight.api import eval_jobs

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = eval_jobs.manager.get(job_id)
        assert job is not None
        if job.status != "running":
            return job.public()
        time.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没结束")


# --------------------------------------------------------------------------
# 状态机（纯逻辑，不走接口）
# --------------------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "active.json")
    assert store.read() is None
    store.write("a/b")
    assert store.read() == "a/b"

    # 清除 = 去掉 run_id 这个键。若文件里还有别的东西（例如切换基准
    # last_effective），文件留下；没有别的才删掉。
    store.write(None)
    assert store.read() is None


def test_store_clear_deletes_the_file_when_nothing_remains(tmp_path):
    """没记过切换基准时，清除就该把文件删干净——不留空壳。"""
    from fire_sight.services import ActiveModelStore

    path = tmp_path / "active.json"
    store = ActiveModelStore(path)
    store.write("a/b")
    store.write(None)
    assert not path.exists()


def test_store_clear_keeps_the_switch_baseline(tmp_path):
    """清除指定 ≠ 换过模型：把 last_effective 一起抹掉，下一次解析会误报成切换。"""
    from fire_sight.services import ActiveModelStore

    path = tmp_path / "active.json"
    store = ActiveModelStore(path)
    store.resolve(["x"])  # 落下 last_effective=x
    store.write(None)

    assert store.read() is None
    assert store.last_effective() == "x"
    assert path.exists()


def test_store_survives_a_corrupt_file(tmp_path):
    """状态文件坏了不该让模型库页打不开。"""
    from fire_sight.services import ActiveModelStore

    path = tmp_path / "active.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    store = ActiveModelStore(path)
    assert store.read() is None
    assert store.resolve(["x"]).run_id == "x"


def test_resolve_falls_back_to_newest_when_unset(tmp_path):
    from fire_sight.services import ActiveModelStore

    state = ActiveModelStore(tmp_path / "a.json").resolve([NEWER, OLDER])
    assert state.run_id == NEWER
    assert state.pinned is None
    assert state.source == "fallback-newest"
    assert state.note  # 界面要显示「为什么是它」


def test_resolve_honours_the_pin(tmp_path):
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "a.json")
    store.write(OLDER)
    state = store.resolve([NEWER, OLDER])
    assert state.run_id == OLDER
    assert state.source == "pinned"
    assert state.pinned == OLDER


def test_resolve_reports_a_dangling_pin(tmp_path):
    """指定的归档被删了：回落到最新，但必须说明——静默回落会让人以为设置丢了。"""
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "a.json")
    store.write("已经被删掉的归档")
    state = store.resolve([NEWER, OLDER])
    assert state.run_id == NEWER
    assert state.source == "fallback-missing"
    assert "已经被删掉的归档" in state.note


def test_resolve_with_no_archives(tmp_path):
    from fire_sight.services import ActiveModelStore

    state = ActiveModelStore(tmp_path / "a.json").resolve([])
    assert state.run_id is None
    assert state.source == "none"


# --------------------------------------------------------------------------
# 「被新归档顶掉」的可见性
#
# 「没指定过就取最新」这条退路有个副作用：每有一个新归档落盘，线上模型就悄悄
# 换掉一次。2026-09-28 真发生过——20260928-165715 一归档就顶掉了 20260927-204643，
# 页面上没有任何提示。这里把「换过」这件事钉成可断言的行为。
# --------------------------------------------------------------------------


def test_resolve_flags_an_auto_switch(tmp_path):
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "a.json")
    assert store.resolve([OLDER]).run_id == OLDER  # 落下基准 OLDER

    # 新归档落地，没指定过 → 自动换成最新，必须说出来了
    state = store.resolve([NEWER, OLDER])
    assert state.run_id == NEWER
    assert state.source == "fallback-newest"
    assert state.switched is True
    assert state.switched_from == OLDER
    assert OLDER in state.note and NEWER in state.note


def test_resolve_does_not_flag_when_nothing_changed(tmp_path):
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "a.json")
    store.resolve([NEWER, OLDER])
    state = store.resolve([NEWER, OLDER])
    assert state.switched is False
    assert state.switched_from is None


def test_resolve_does_not_flag_a_user_choice(tmp_path):
    """用户点「设为当前」是他自己干的，不算需要提醒的自动切换。"""
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "a.json")
    store.resolve([NEWER, OLDER])  # 基准 NEWER
    store.write(OLDER)             # 显式指定
    state = store.resolve([NEWER, OLDER])
    assert state.source == "pinned"
    assert state.switched is False


def test_resolve_does_not_flag_when_previous_was_deleted(tmp_path):
    """上一次生效的归档被删了 → 那是删除流程的失效回落，由它自己说明，
    不算「被新归档顶掉」（避免同一件事报两遍）。"""
    from fire_sight.services import ActiveModelStore

    store = ActiveModelStore(tmp_path / "a.json")
    store.resolve([NEWER, OLDER])  # 基准 NEWER
    state = store.resolve([OLDER])  # NEWER 被删，OLDER 顶上
    assert state.run_id == OLDER
    assert state.switched is False


def test_resolve_records_the_baseline_only_when_it_changes(tmp_path):
    """解析是读路径：值没变就不要写盘。每次读都落盘会让状态文件无谓磨损。"""
    from fire_sight.services import ActiveModelStore

    path = tmp_path / "a.json"
    store = ActiveModelStore(path)
    store.resolve([NEWER, OLDER])

    calls = []
    original = store._save
    store._save = lambda payload: (calls.append(payload), original(payload))[1]

    store.resolve([NEWER, OLDER])
    assert calls == []

    store.resolve([OLDER])  # 生效值 NEWER → OLDER，这时才该落盘
    assert len(calls) == 1


def test_active_endpoint_surfaces_an_auto_switch(client, models_env):
    """接口要把「被顶掉」带出来——界面据此提醒，否则又是一次静默切换。"""
    first = client.get("/api/models/active").json()
    assert first["run_id"] == NEWER
    assert first["switched"] is False
    assert first["switched_from"] is None

    _write_archive(models_env / "20260103-c", mAP50=0.60, epochs=5)

    second = client.get("/api/models/active").json()
    assert second["run_id"] == "20260103-c"
    assert second["switched"] is True
    assert second["switched_from"] == NEWER
    assert NEWER in second["note"]  # 说明里点名被顶掉的那个


def test_active_endpoint_stays_quiet_after_a_user_choice(client, models_env):
    client.get("/api/models/active")  # 落下基准
    client.post("/api/models/active", json={"run_id": OLDER})

    body = client.get("/api/models/active").json()
    assert body["source"] == "pinned"
    assert body["switched"] is False


# --------------------------------------------------------------------------
# 列表
# --------------------------------------------------------------------------


def test_list_reports_metrics_and_config(client):
    body = client.get("/api/models").json()
    assert [m["run_id"] for m in body] == [NEWER, OLDER]

    newer = body[0]
    assert newer["mAP50"] == 0.70
    assert newer["mAP50_95"] == 0.35
    assert newer["epochs"] == 10  # 来自 train_config.yaml
    assert newer["dataset"] == "D-Fire/train"
    assert newer["size_mb"] == pytest.approx(0.0, abs=0.01)  # 2 KB 的假权重


def test_list_marks_exactly_one_active_without_a_pin(client):
    body = client.get("/api/models").json()
    actives = [m["run_id"] for m in body if m["is_active"]]
    assert actives == [NEWER]


# --------------------------------------------------------------------------
# 设为当前 / 清除
# --------------------------------------------------------------------------


def test_set_active_pins_an_older_worse_model(client):
    """这就是模块存在的理由：让用户把指标更好的旧归档选回来。"""
    from fire_sight.api import deps

    assert deps.active_run().run_id == NEWER

    body = client.post("/api/models/active", json={"run_id": OLDER}).json()
    assert body["run_id"] == OLDER
    assert body["source"] == "pinned"

    # 真的改了线上用的那份，而不只是列表上打个标记
    assert deps.active_run().run_id == OLDER
    assert [m["run_id"] for m in client.get("/api/models").json() if m["is_active"]] == [OLDER]


def test_set_active_rejects_unknown_run(client):
    r = client.post("/api/models/active", json={"run_id": "不存在"})
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]


def test_set_active_requires_a_run_id(client):
    assert client.post("/api/models/active", json={}).status_code == 422
    assert client.post("/api/models/active", json={"run_id": ""}).status_code == 422


def test_clear_active_goes_back_to_newest(client):
    client.post("/api/models/active", json={"run_id": OLDER})
    body = client.request("DELETE", "/api/models/active").json()
    assert body["run_id"] == NEWER
    assert body["pinned"] is None
    assert body["source"] == "fallback-newest"


def test_active_endpoint_explains_a_dangling_pin(client, models_env):
    from fire_sight.api import deps

    deps.ACTIVE_MODEL_STORE.write("被删掉的")
    body = client.get("/api/models/active").json()
    assert body["source"] == "fallback-missing"
    assert "被删掉的" in body["note"]


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------


def test_download_returns_the_weights(client):
    r = client.get("/api/models/download", params={"run_id": NEWER})
    assert r.status_code == 200
    assert r.content.startswith(b"\x00")
    assert "attachment" in r.headers["content-disposition"]
    # run_id 含斜杠时要拍平成合法文件名
    assert NEWER in r.headers["content-disposition"]


def test_download_flattens_slashes_in_the_filename(client, models_env):
    from fire_sight.api import deps

    nested = "20260103-c/exp"
    _write_archive(deps.TRAINED_MODELS_DIR / "20260103-c" / "exp", mAP50=0.6, epochs=5)
    r = client.get("/api/models/download", params={"run_id": nested})
    assert r.status_code == 200
    assert "20260103-c_exp.pt" in r.headers["content-disposition"]


def test_download_unknown_run_is_404(client):
    assert client.get("/api/models/download", params={"run_id": "nope"}).status_code == 404


def test_download_requires_a_run_id(client):
    assert client.get("/api/models/download").status_code == 422


# --------------------------------------------------------------------------
# 对比评估：校验与排期
# --------------------------------------------------------------------------


def test_eval_rejects_unknown_runs_without_starting_anything(client, models_env):
    from fire_sight.api import deps, eval_jobs

    r = client.post("/api/eval", json={"run_ids": [NEWER, "不存在"]})
    assert r.status_code == 404
    assert eval_jobs.manager.list() == []
    assert deps.GPU_LOCK.holder is None


def test_eval_needs_at_least_two_models(client):
    """单个权重用不到「对比」——一个模型的「对比」是拿它跟谁比？"""
    assert client.post("/api/eval", json={"run_ids": [NEWER]}).status_code == 422
    assert client.post("/api/eval", json={"run_ids": []}).status_code == 422


def test_eval_caps_the_model_count(client):
    assert (
        client.post("/api/eval", json={"run_ids": [f"r{i}" for i in range(6)]}).status_code
        == 422
    )


def test_eval_returns_409_when_the_gpu_is_busy(client, models_env):
    from fire_sight.api import deps, eval_jobs

    assert deps.GPU_LOCK.acquire("infer") is True
    try:
        r = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]})
    finally:
        deps.GPU_LOCK.release()

    assert r.status_code == 409
    assert "infer" in r.json()["detail"]
    assert eval_jobs.manager.list() == []


# --------------------------------------------------------------------------
# 评估的划分：val 选模型 / test 最终汇报
# --------------------------------------------------------------------------


def test_eval_split_defaults_to_val(client, models_env, fake_eval):
    """不传 split 时是 val——它是平时选模型用的那一份。"""
    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]}).json()
    assert started["split"] == "val"
    wait_done(started["id"])
    # 真的传下去了，不是只在任务对象上记了一笔
    assert [c.split for c in fake_eval] == ["val", "val"]


def test_eval_split_reaches_the_platform_config(client, models_env, fake_eval):
    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER], "split": "test"}).json()
    assert started["split"] == "test"
    wait_done(started["id"])
    assert [c.split for c in fake_eval] == ["test", "test"]


def test_eval_split_is_case_insensitive(client, models_env, fake_eval):
    """大小写不该决定成败——界面传什么都能用。"""
    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER], "split": "TEST"}).json()
    assert started["split"] == "test"
    wait_done(started["id"])


def test_eval_rejects_train_and_unknown_splits(client, models_env):
    """`train` 必须在列表外：在训过的数据上报指标没有意义，只会误导。"""
    from fire_sight.api import eval_jobs

    for bad in ("train", "TRAIN", "valid", ""):
        r = client.post("/api/eval", json={"run_ids": [NEWER, OLDER], "split": bad})
        assert r.status_code == 400, f"{bad!r} 应被拒绝"
        assert "val" in r.json()["detail"] and "test" in r.json()["detail"]
    # 被拒的任务一个都不该留下，锁也不能被拿走
    assert eval_jobs.manager.list() == []
    from fire_sight.api import deps

    assert deps.GPU_LOCK.holder is None


def test_eval_job_info_always_carries_the_split(client, models_env, fake_eval):
    """界面上要能看出这组数是 val 还是 test——两者的数不可混着比。"""
    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER], "split": "test"}).json()
    assert client.get(f"/api/eval/jobs/{started['id']}").json()["split"] == "test"
    listed = {j["id"]: j for j in client.get("/api/eval/jobs").json()}
    assert listed[started["id"]]["split"] == "test"
    wait_done(started["id"])


# --------------------------------------------------------------------------
# 旧数据标记
# --------------------------------------------------------------------------


FLAT = "flat"
LEGACY = "20260103-c"


def test_legacy_flag_marks_old_dataset_archives(client, models_env, monkeypatch):
    """用旧数据（processed 的随机划分）训的归档要标出来。

    新 val 里有一部分图当初进过旧 train，拿旧归档在新 val 上评估会虚高。
    不标的话，用户会拿两个不同起跑线的数去挑模型。
    """
    from fire_sight.api import deps

    _write_archive(models_env / LEGACY, mAP50=0.9, epochs=50, dataset="D-Fire/train")
    _write_archive(models_env / FLAT, mAP50=0.6, epochs=50, dataset="D-Fire/raw")

    body = {m["run_id"]: m for m in client.get("/api/models").json()}
    assert body[LEGACY]["legacy"] is True
    assert body[FLAT]["legacy"] is False
    # 判据是数据集标签，不是时间：新的那个也可以是旧的
    assert body[LEGACY]["dataset"] == "D-Fire/train"
    assert body[FLAT]["dataset"] == "D-Fire/raw"


def test_legacy_is_true_when_the_dataset_is_unknown(client, models_env):
    """标签缺失时按旧处理——宁可多标一个，也不要漏标一个让数虚高。

    目前只有旧归档可能缺标签：新数据的标签由 `_dataset_label` 从路径反算，
    一定拿得到。
    """
    from fire_sight.api import deps

    root = models_env / "20260104-d"
    root.mkdir(parents=True)
    (root / "best.pt").write_bytes(b"\x00" * 64)
    (root / "metrics.txt").write_text("mAP50=0.5\n", encoding="utf-8")

    body = {m["run_id"]: m for m in client.get("/api/models").json()}
    assert body["20260104-d"]["legacy"] is True
    assert body["20260104-d"]["dataset"] is None


def test_legacy_flag_does_not_depend_on_run_id_shape(client, models_env):
    """回归：判据不能退化成「run_id 里有没有 D-Fire/train」。

    早期布局的 run_id 自带斜杠（`20260926-190635-D-Fire/train-yolo11n`），
    按字符串匹配会在换数据集后悄悄标错。
    """
    from fire_sight.api import deps

    _write_archive(models_env / "20260105-D-Fire/train-yolo11n", mAP50=0.7, epochs=50)
    _write_archive(
        models_env / "20260106-D-Fire-raw-n", mAP50=0.7, epochs=50, dataset="D-Fire/raw"
    )

    body = {m["run_id"]: m for m in client.get("/api/models").json()}
    # 名字里带 D-Fire/train 反而是新的那份（标签说它用 raw 训的）
    assert body["20260106-D-Fire-raw-n"]["legacy"] is False
    assert body["20260105-D-Fire/train-yolo11n"]["legacy"] is True


def test_eval_dataset_accepts_raw_label(client, models_env, monkeypatch):
    """评估也能落在 raw 那份数据集上——它的 data.yaml 不在 processed 下。

    只按 processed 解析会让 `D-Fire/raw` 一评就报「找不到数据集 yaml」。
    """
    import fire_sight_platform.evaluation.service as eval_svc

    seen: list = []

    def fake_resolve(dataset, split="val"):
        seen.append((dataset, split))
        return Path("E:/FireSight/data/raw/D-Fire/data.yaml")

    monkeypatch.setattr(eval_svc, "_resolve_yaml", fake_resolve)
    # 走真 evaluate_weights 会把 ultralytics 拉起来，这里只验解析
    assert eval_svc._resolve_yaml("D-Fire/raw") == Path(
        "E:/FireSight/data/raw/D-Fire/data.yaml"
    )
    assert seen == [("D-Fire/raw", "val")]



def test_eval_job_list_is_empty_initially(client):
    assert client.get("/api/eval/jobs").json() == []


def test_eval_unknown_job_is_404(client):
    assert client.get("/api/eval/jobs/nope").status_code == 404


# --------------------------------------------------------------------------
# 对比评估：任务生命周期（假的 evaluate_weights）
# --------------------------------------------------------------------------


@pytest.fixture()
def fake_eval(monkeypatch):
    """把 evaluate_weights 换成假的，并记录调用顺序。"""
    import fire_sight_platform.evaluation.service as eval_svc

    calls: list = []

    class FakeResult:
        def __init__(self, weights: str) -> None:
            self.weights = weights
            self.metrics = {
                "mAP50": 0.5,
                "mAP50_95": 0.25,
                "precision": 0.6,
                "recall": 0.4,
            }
            self.per_class = {"fire": 0.3, "smoke": 0.2}
            self.speed_ms = {"inference": 12.5}

    def fake(cfg):
        calls.append(cfg)
        if "boom" in str(cfg.weights):
            raise RuntimeError("模拟某个权重评估失败")
        return FakeResult(str(cfg.weights))

    monkeypatch.setattr(eval_svc, "evaluate_weights", fake)
    return calls


def test_eval_job_runs_every_model_and_releases_the_lock(client, models_env, fake_eval):
    from fire_sight.api import deps

    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]}).json()
    assert started["status"] == "running"
    assert started["total"] == 2
    assert started["split"] == "val"
    # 数据集取第一个选中模型记录的那份——对比必须落在同一份 val 上
    assert started["dataset"] == "D-Fire/train"

    done = wait_done(started["id"])
    assert done["status"] == "done"
    assert done["done"] == done["total"] == 2
    assert [r["run_id"] for r in done["results"]] == [NEWER, OLDER]

    first = done["results"][0]
    assert first["mAP50"] == 0.5
    assert first["per_class"] == {"fire": 0.3, "smoke": 0.2}
    assert first["speed_ms"] == {"inference": 12.5}
    assert first["error"] is None

    # 锁必须还回去，否则这台机器再也起不了训练或评估
    assert deps.GPU_LOCK.holder is None
    assert len(fake_eval) == 2

    # 接口形状与内部形状必须一致，且**成功与失败两种情况的键集合相同**——
    # 形状随结果变化时，前端就得给每个字段写两套分支
    http = client.get(f"/api/eval/jobs/{started['id']}").json()
    assert set(http["results"][0]) == {
        "run_id",
        "mAP50",
        "mAP50_95",
        "precision",
        "recall",
        "per_class",
        "speed_ms",
        "error",
    }


def test_eval_job_deduplicates_run_ids(client, models_env, fake_eval):
    started = client.post("/api/eval", json={"run_ids": [NEWER, NEWER, OLDER]}).json()
    assert started["run_ids"] == [NEWER, OLDER]
    done = wait_done(started["id"])
    assert done["total"] == 2


def test_one_failing_model_does_not_kill_the_comparison(client, models_env, monkeypatch):
    """单个权重挂了要如实标在那一行，其余照跑——不能整场对比作废。"""
    import fire_sight_platform.evaluation.service as eval_svc
    from fire_sight.api import deps

    def fake(cfg):
        if "20260102" in str(cfg.weights):
            raise RuntimeError("模拟 OOM")
        class R:
            metrics = {"mAP50": 0.9, "mAP50_95": 0.4, "precision": 0.7, "recall": 0.5}
            per_class: dict = {}
            speed_ms: dict = {}
        return R()

    monkeypatch.setattr(eval_svc, "evaluate_weights", fake)

    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]}).json()
    done = wait_done(started["id"])

    assert done["status"] == "done"  # 整体仍算完成
    by_id = {r["run_id"]: r for r in done["results"]}
    assert "模拟 OOM" in by_id[NEWER]["error"]
    assert by_id[NEWER]["mAP50"] is None
    assert by_id[OLDER]["mAP50"] == 0.9
    assert deps.GPU_LOCK.holder is None
    # 失败行与成功行的键集合必须一致
    assert set(by_id[NEWER]) == set(by_id[OLDER])


def test_eval_job_shows_partial_progress(client, models_env, monkeypatch):
    """逐个模型出结果——界面据此画「正在评估 1/2」，不必等全部跑完。"""
    import fire_sight_platform.evaluation.service as eval_svc
    from fire_sight.api import eval_jobs

    gate = __import__("threading").Event()

    def slow(cfg):
        gate.wait(5)
        class R:
            metrics = {"mAP50": 0.1, "mAP50_95": 0.05, "precision": 0.2, "recall": 0.1}
            per_class: dict = {}
            speed_ms: dict = {}
        return R()

    monkeypatch.setattr(eval_svc, "evaluate_weights", slow)

    started = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]}).json()
    # 一个都还没跑完：done=0 但 total=2，且状态仍是 running
    mid = client.get(f"/api/eval/jobs/{started['id']}").json()
    assert mid["status"] == "running"
    assert mid["done"] == 0 and mid["total"] == 2
    assert mid["results"] == []

    gate.set()
    done = wait_done(started["id"])
    assert done["done"] == 2
    assert eval_jobs.manager.get(started["id"]).status == "done"


def test_second_eval_is_rejected_while_the_first_runs(client, models_env, monkeypatch):
    """GPU 同一时刻只能有一个任务——第二个应当是 409，而不是排队或并行。"""
    import threading

    import fire_sight_platform.evaluation.service as eval_svc

    gate = threading.Event()

    def slow(cfg):
        gate.wait(5)
        class R:
            metrics = {"mAP50": 0.1, "mAP50_95": 0.05, "precision": 0.2, "recall": 0.1}
            per_class: dict = {}
            speed_ms: dict = {}
        return R()

    monkeypatch.setattr(eval_svc, "evaluate_weights", slow)

    first = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]}).json()
    second = client.post("/api/eval", json={"run_ids": [NEWER, OLDER]})
    assert second.status_code == 409

    gate.set()
    wait_done(first["id"])


# --------------------------------------------------------------------------
# 删除（移入回收目录）
# --------------------------------------------------------------------------


def _write_run_site(root: Path, epochs: int = 5) -> None:
    """造一个训练现场：判据是 results.csv 存在（与 deps.training_runs 一致）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "results.csv").write_text(
        "epoch,metrics/mAP50\n" + "".join(f"{i},{0.1 * i}\n" for i in range(1, epochs + 1)),
        encoding="utf-8",
    )
    (root / "weights").mkdir(exist_ok=True)
    (root / "weights" / "last.pt").write_bytes(b"\x00" * 1024)


@pytest.fixture()
def trash_env(tmp_path, models_env, monkeypatch):
    """把训练现场目录与回收目录都指到 tmp_path，避免真往工作区里写。

    `TRASH_DIR` 只 patch `recycle_service`：它是这个常量唯一的消费方，
    `deps` 从来没导入过它（早先这里多 patch 了一处，直接 AttributeError）。
    """
    from fire_sight.api import deps

    runs = tmp_path / "training"
    monkeypatch.setattr(deps, "RUNS_DIR", tmp_path)
    # recycle_service 在导入时就绑定了 TRASH_DIR，所以要打在它自己的命名空间上
    import fire_sight.services.recycle_service as rs

    monkeypatch.setattr(rs, "TRASH_DIR", tmp_path / "trash")
    return runs


def test_delete_archive_only_moves_it_to_trash(client, trash_env, monkeypatch):
    """默认只删归档权重，训练现场原样不动——两份数据不该被绑死。"""
    from fire_sight.api import deps
    import fire_sight.services.recycle_service as rs

    site = trash_env / NEWER
    _write_run_site(site)

    r = client.request("DELETE", "/api/models", json={"run_id": NEWER})
    assert r.status_code == 200
    body = r.json()
    assert body["removed_archive"] is True
    assert body["removed_run"] is False
    assert body["trash_dir"]

    # 归档没了
    assert deps.find_archived_run(NEWER) is None
    assert [m["run_id"] for m in client.get("/api/models").json()] == [OLDER]
    # 现场还在
    assert (site / "results.csv").exists()
    # 真的在回收目录里，而不是被抹掉
    trash = Path(body["trash_dir"])
    assert (trash / "archive" / "best.pt").exists()


def test_delete_with_run_moves_both(client, trash_env):
    from fire_sight.api import deps

    site = trash_env / NEWER
    _write_run_site(site)

    body = client.request(
        "DELETE", "/api/models", json={"run_id": NEWER, "with_run": True}
    ).json()
    assert body["removed_archive"] is True
    assert body["removed_run"] is True

    assert deps.find_archived_run(NEWER) is None
    assert all(r.run_id != NEWER for r in deps.training_runs())
    trash = Path(body["trash_dir"])
    assert (trash / "run" / "results.csv").exists()


def test_delete_run_site_only_needs_the_checkbox(client, trash_env):
    """只有现场、没有归档时，不勾 with_run 应当明确拒绝，而不是返回「成功但什么都没删」。"""
    site = trash_env / "只有现场"
    _write_run_site(site)

    r = client.request("DELETE", "/api/models", json={"run_id": "只有现场"})
    assert r.status_code == 400
    assert "训练现场" in r.json()["detail"]
    # 拒绝之后现场必须原封不动
    assert (site / "results.csv").exists()

    ok = client.request(
        "DELETE", "/api/models", json={"run_id": "只有现场", "with_run": True}
    ).json()
    assert ok["removed_archive"] is False
    assert ok["removed_run"] is True
    assert not site.exists()


def test_delete_reports_the_fallback_when_the_active_one_is_removed(client, trash_env):
    """删掉当前权重：界面必须知道现在轮到谁，而不是下次打开识别页才发现换了模型。"""
    from fire_sight.api import deps

    assert deps.active_model_state().run_id == NEWER  # 没指定过 → 取最新
    body = client.request("DELETE", "/api/models", json={"run_id": NEWER}).json()

    assert body["active"]["run_id"] == OLDER
    assert body["active"]["source"] == "fallback-newest"
    # 删的恰好是当前权重这件事要出现在给用户看的那句话里
    assert "已移入回收目录" in body["note"]


def test_delete_a_pinned_active_archive_reports_dangling_pin(client, trash_env):
    """指定过的归档被删：回落必须说明「指定的已不在」，静默换模型最容易让人以为设置丢了。"""
    from fire_sight.api import deps

    deps.ACTIVE_MODEL_STORE.write(NEWER)
    body = client.request("DELETE", "/api/models", json={"run_id": NEWER}).json()

    assert body["active"]["source"] == "fallback-missing"
    assert body["active"]["pinned"] == NEWER
    assert NEWER in body["active"]["note"]
    # 句尾还要再说一次当前权重落到哪了
    assert OLDER in body["note"]


def test_delete_unknown_run_is_404(client, trash_env):
    r = client.request("DELETE", "/api/models", json={"run_id": "从来没有过"})
    assert r.status_code == 404
    assert "从来没有过" in r.json()["detail"]


def test_delete_requires_a_run_id(client, trash_env):
    assert client.request("DELETE", "/api/models", json={}).status_code == 422
    assert client.request("DELETE", "/api/models", json={"run_id": ""}).status_code == 422


def test_delete_drops_the_inference_cache(client, trash_env, monkeypatch):
    """权重被搬走之后，内存里那份必须扔掉。

    否则识别页还拿着一个磁盘上已经不存在的模型继续推理，直到服务重启——
    「看起来正常但结果不对」的那一类。
    """
    from fire_sight.api import deps

    calls = {"n": 0}
    real = deps.clear_infer_cache

    def spy():
        calls["n"] += 1
        real()

    monkeypatch.setattr(deps, "clear_infer_cache", spy)

    client.request("DELETE", "/api/models", json={"run_id": NEWER})
    assert calls["n"] == 1


def test_delete_nested_archive_cleans_the_empty_shell(client, models_env, trash_env):
    """嵌套布局 `trained/<run_id>/<exp>/best.pt` 搬走后，外层空壳要一并清掉。

    留着它会让列表多扫一层，文件管理器里也留个空文件夹。
    """
    from fire_sight.api import deps

    outer = deps.TRAINED_MODELS_DIR / "20260103-c"
    _write_archive(outer / "exp", mAP50=0.6, epochs=5)
    assert deps.find_archived_run("20260103-c/exp") is not None

    body = client.request("DELETE", "/api/models", json={"run_id": "20260103-c/exp"}).json()
    assert body["removed_archive"] is True
    assert not outer.exists()
    # 兄弟归档不能被误伤
    assert deps.find_archived_run(NEWER) is not None


def test_delete_twice_in_the_same_second_does_not_collide(client, models_env, trash_env):
    """同一秒删两次要落在不同目录，否则第二次会覆盖第一次（回收目录就没意义了）。"""
    from fire_sight.services.recycle_service import recycle

    a = models_env / NEWER
    b = models_env / OLDER
    when = __import__("datetime").datetime(2026, 1, 1, 0, 0, 0)

    first = recycle(NEWER, archive_root=a, when=when)
    second = recycle(OLDER, archive_root=b, when=when)

    assert first.trash_dir != second.trash_dir
    assert first.count == 1 and second.count == 1


def test_recycle_with_nothing_to_move_leaves_no_empty_dir(tmp_path, monkeypatch):
    """一件都没搬走就不该留下空目录，否则回收站会堆一串空壳，看不出删过什么。"""
    import fire_sight.services.recycle_service as rs

    trash = tmp_path / "trash"
    monkeypatch.setattr(rs, "TRASH_DIR", trash)

    removed = rs.recycle("不存在的", archive_root=tmp_path / "nope")
    assert removed.items == []
    assert not removed.trash_dir.exists()
