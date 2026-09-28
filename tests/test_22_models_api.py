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
    # 清除 = 删文件（文件存在即「指定过」，语义只有一种）
    store.write(None)
    assert store.read() is None
    assert not (tmp_path / "active.json").exists()


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
