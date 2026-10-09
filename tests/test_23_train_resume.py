"""断点续训：检查点判据、拒绝路径、以及路由交给子进程的配置。

**不真跑训练**：把 `manager.start` 换掉，只断言它收到的配置对不对。真续训要几十分钟，
而且这套逻辑的正确性全在「配置怎么拼、什么时候拒绝」上。

判据的核心是 ultralytics 的一条实际行为：`Trainer.final_eval()` 会调
`strip_optimizer(last)`，所以**跑完的现场只剩权重**——从它「续训」等于换个权重微调，
学习率轨迹接不上。这条必须能被区分出来，否则用户会以为自己在续训。
"""

from __future__ import annotations

import json
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
    "val/box_loss,val/cls_loss,val/dfl_loss\n"
)

RUN_ID = "20260101-120000-D-Fire-train-yolo11s"
STRIPPED_ID = "20260102-120000-D-Fire-train-yolo11n"


def make_checkpoint(path: Path, *, done_epochs: int, total: int, stripped: bool) -> None:
    """造一个检查点。stripped=True 模拟跑完的现场（优化器已被剥离）。"""
    import torch

    from fire_sight_platform.common.paths import PROCESSED_DATA_DIR

    path.parent.mkdir(parents=True, exist_ok=True)
    ckpt = {
        "epoch": done_epochs - 1,  # ultralytics 里是 0-based
        "updates": 1000 + done_epochs,
        "best_fitness": 0.4,
        "train_args": {
            # 真实检查点记的是解析后的 yaml 路径；界面把它收成 `D-Fire/train`
            "data": str(PROCESSED_DATA_DIR / "D-Fire" / "train" / "data.yaml"),
            "model": "yolo11s.pt",
            "epochs": total,
            "imgsz": 512,
            "batch": 24,
            "device": "0",
            "project": str(path.parent.parent),
            "name": path.parent.parent.name,
        },
        "ema": {"fake": "ema"},
        "optimizer": None if stripped else {"param_groups": [], "state": {}},
    }
    torch.save(ckpt, str(path))


def make_run(root: Path, run_id: str, *, done_epochs: int, total: int, stripped: bool) -> Path:
    """造一个训练现场：results.csv（列表接口的判据）+ weights/last.pt + args.yaml。"""
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{i},{i * 10},1.0,1.0,1.0,0.5,0.4,0.3,0.2,1,1,1\n" for i in range(1, done_epochs + 1))
    (run / "results.csv").write_text(HEADER + rows, encoding="utf-8")
    (run / "args.yaml").write_text(
        f"model: yolo11s.pt\ndata: E:/x/data/processed/D-Fire/train/data.yaml\nepochs: {total}\n",
        encoding="utf-8",
    )
    make_checkpoint(run / "weights" / "last.pt", done_epochs=done_epochs, total=total, stripped=stripped)
    return run


@pytest.fixture()
def runs_env(tmp_path, monkeypatch):
    """两个现场：一个可续（被中断），一个不可续（跑完、优化器已剥离）。"""
    from fire_sight.api import deps

    root = tmp_path / "runs" / "training"
    root.mkdir(parents=True)
    monkeypatch.setattr(deps, "RUNS_DIR", tmp_path / "runs")

    make_run(root, RUN_ID, done_epochs=40, total=50, stripped=False)
    make_run(root, STRIPPED_ID, done_epochs=30, total=30, stripped=True)
    return root


@pytest.fixture()
def client(runs_env):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    return TestClient(app)


@pytest.fixture()
def captured(monkeypatch):
    """替换 manager.start，只记下它收到的配置，不起进程。"""
    from fire_sight.api.routers import train as train_router

    seen: dict = {}

    class FakeJob:
        id = "fake-job"
        status = "running"

        def __init__(self, config):
            self.config = config

        def public(self):
            return {"id": self.id, "status": self.status, "config": self.config}

    def fake_start(config):
        seen.update(config)
        return FakeJob(config)

    monkeypatch.setattr(train_router.manager, "start", fake_start)
    return seen


# --------------------------------------------------------------------------
# 检查点判据
# --------------------------------------------------------------------------


def test_read_checkpoint_reports_full_state(tmp_path):
    from fire_sight_platform.training.service import read_checkpoint

    path = tmp_path / "weights" / "last.pt"
    make_checkpoint(path, done_epochs=40, total=50, stripped=False)
    info = read_checkpoint(path)

    assert info["done_epochs"] == 40
    assert info["total_epochs"] == 50
    assert info["imgsz"] == 512 and info["batch"] == 24
    assert info["model"] == "yolo11s.pt"
    assert info["has_optimizer"] is True
    assert info["has_ema"] is True


def test_read_checkpoint_detects_stripped_state(tmp_path):
    from fire_sight_platform.training.service import read_checkpoint

    path = tmp_path / "weights" / "last.pt"
    make_checkpoint(path, done_epochs=30, total=30, stripped=True)
    info = read_checkpoint(path)

    assert info["has_optimizer"] is False
    assert info["done_epochs"] == 30


# --------------------------------------------------------------------------
# 列表里的可续训标记
# --------------------------------------------------------------------------


def test_list_marks_interrupted_run_resumable(client):
    body = client.get("/api/train/runs").json()
    by_id = {r["run_id"]: r for r in body}

    run = by_id[RUN_ID]
    assert run["resumable"] is True
    assert run["resume_note"] == ""
    assert run["resume_done"] == 40
    assert run["resume_total"] == 50


def test_list_marks_completed_run_unresumable_with_reason(client):
    body = client.get("/api/train/runs").json()
    run = {r["run_id"]: r for r in body}[STRIPPED_ID]

    assert run["resumable"] is False
    # 理由要说人话：不能只给一个禁用的按钮
    assert "优化器" in run["resume_note"]


def test_list_marks_run_without_checkpoint(client, runs_env):
    from fire_sight.api import deps

    run_id = "20260103-120000-D-Fire-train-yolo11n"
    make_run(runs_env, run_id, done_epochs=3, total=10, stripped=False)
    (runs_env / run_id / "weights" / "last.pt").unlink()

    body = client.get("/api/train/runs").json()
    run = {r["run_id"]: r for r in body}[run_id]
    assert run["resumable"] is False
    assert "last.pt" in run["resume_note"]
    assert deps is not None


# --------------------------------------------------------------------------
# 发起续训
# --------------------------------------------------------------------------


def test_resume_passes_the_checkpoint_geometry_not_the_request(client, captured):
    """几何、模型、总轮数必须取自检查点——请求里给的是误导。"""
    r = client.post(
        "/api/train/jobs",
        json={
            "dataset": "随便",
            "resume_run_id": RUN_ID,
            # 下面这些是「谎话」：后端不能信
            "model": "yolo11n.pt",
            "imgsz": 1280,
            "batch": 2,
            "epochs": 7,
        },
    )
    assert r.status_code == 201, r.text

    assert captured["resume"].endswith("last.pt")
    assert RUN_ID in captured["resume"]
    assert captured["imgsz"] == 512          # 检查点里的
    assert captured["batch"] == 24           # 检查点里的
    assert captured["model"] == "yolo11s.pt"  # 检查点里的
    assert captured["epochs"] == 50          # 总轮数，供界面显示
    assert captured["dataset"] == "D-Fire/train"


def test_resume_unknown_run_is_404(client, captured):
    r = client.post("/api/train/jobs", json={"dataset": "x", "resume_run_id": "不存在"})
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]
    assert captured == {}, "被拒的请求不该起任务"


def test_resume_completed_run_is_400_with_reason(client, captured):
    """跑完的现场不能续——优化器状态已被剥离，得说清而不是让它跑起来。"""
    r = client.post("/api/train/jobs", json={"dataset": "x", "resume_run_id": STRIPPED_ID})
    assert r.status_code == 400
    assert "优化器" in r.json()["detail"]
    assert captured == {}


def test_resume_without_last_pt_is_404(client, runs_env, captured):
    run_id = "20260104-120000-D-Fire-train-yolo11n"
    make_run(runs_env, run_id, done_epochs=3, total=10, stripped=False)
    (runs_env / run_id / "weights" / "last.pt").unlink()

    r = client.post("/api/train/jobs", json={"dataset": "x", "resume_run_id": run_id})
    assert r.status_code == 404
    assert "last.pt" in r.json()["detail"]
    assert captured == {}


def test_resume_guard_uses_the_checkpoint_model(client, runs_env, captured, monkeypatch):
    """守卫必须按检查点的模型判规模因子。

    否则会拿 yolo11n 的额度放行一个 yolo11s 的续训（实测差 1.82 倍）。
    这里造一个「yolo11m + 大 batch」的检查点，它应当被拒。
    """
    from fire_sight.api import deps

    run_id = "20260105-120000-D-Fire-train-yolo11m"
    run = make_run(runs_env, run_id, done_epochs=5, total=30, stripped=False)
    import torch

    ckpt = torch.load(run / "weights" / "last.pt", map_location="cpu", weights_only=False)
    ckpt["train_args"].update({"model": "yolo11m.pt", "imgsz": 640, "batch": 32})
    torch.save(ckpt, str(run / "weights" / "last.pt"))

    monkeypatch.setattr(
        deps,
        "gpu_info",
        lambda: {
            "available": True,
            "name": "fake",
            "memory_total_mb": 8188,
            "memory_used_mb": 0,
            "utilization_pct": 0,
        },
    )

    r = client.post(
        "/api/train/jobs",
        json={"dataset": "x", "resume_run_id": run_id, "model": "yolo11n.pt", "imgsz": 320, "batch": 1},
    )
    assert r.status_code == 400, r.text
    assert "yolo11m" in r.json()["detail"]
    assert captured == {}


def test_resume_allows_device_override(client, captured):
    """设备是允许覆盖的白名单项——重启后换 CPU 续训是合理需求。"""
    r = client.post(
        "/api/train/jobs",
        json={"dataset": "x", "resume_run_id": RUN_ID, "device": "cpu"},
    )
    assert r.status_code == 201, r.text
    assert captured["device"] == "cpu"
    assert captured["resume"]


# --------------------------------------------------------------------------
# 子进程侧的拒绝（同一判据的第二道）
# --------------------------------------------------------------------------


def test_train_from_checkpoint_refuses_a_stripped_checkpoint(tmp_path):
    from fire_sight_platform.runtime_config.base import TrainConfig
    from fire_sight_platform.training.service import _train_from_checkpoint

    path = tmp_path / "weights" / "last.pt"
    make_checkpoint(path, done_epochs=30, total=30, stripped=True)

    with pytest.raises(ValueError, match="优化器"):
        _train_from_checkpoint(TrainConfig(resume=str(path)), None)


def test_resume_guard_survives_a_checkpoint_rewritten_by_a_previous_resume(
    client, runs_env, captured, monkeypatch
):
    """第二次续训也要认得出模型。

    回归：续训一次后，ultralytics 会把检查点里的 `train_args.model` 改写成
    `.../weights/last.pt`（`check_resume` 里 `self.args.model = self.args.resume`）。
    只看那个字段会把模型认成 `last` → 守卫按未知模型（yolo11m 的 3.83 倍）估 →
    **一个本来合法的续训被拒**。实测就是这个把续训卡住的：
    「batch=16 × imgsz=640 在 last 上……超过预算……imgsz=640 时 batch 最多 10」。
    """
    from fire_sight.api import deps

    run_id = "20260106-120000-D-Fire-train-yolo11s"
    run = make_run(runs_env, run_id, done_epochs=41, total=50, stripped=False)
    import torch

    ckpt = torch.load(run / "weights" / "last.pt", map_location="cpu", weights_only=False)
    # 上一次续训留下的痕迹
    ckpt["train_args"]["model"] = str(run / "weights" / "last.pt")
    torch.save(ckpt, str(run / "weights" / "last.pt"))

    monkeypatch.setattr(
        deps,
        "gpu_info",
        lambda: {
            "available": True,
            "name": "fake",
            "memory_total_mb": 8188,
            "memory_used_mb": 0,
            "utilization_pct": 0,
        },
    )

    r = client.post("/api/train/jobs", json={"dataset": "x", "resume_run_id": run_id})
    assert r.status_code == 201, r.text
    assert captured["model"] == "yolo11s.pt"  # 认出来是 yolo11s，不是 last


def test_checkpoint_model_prefers_the_run_id():
    from fire_sight.api.routers.train import _checkpoint_model

    # run_id 末段是已知模型 → 用它（即使检查点里已被改写成 last.pt）
    assert (
        _checkpoint_model({"model": "x/weights/last.pt"}, "20260101-120000-D-Fire-train-yolo11s")
        == "yolo11s.pt"
    )
    # run_id 末段不认识 → 退回检查点里的名字
    assert _checkpoint_model({"model": "yolo11m.pt"}, "probe-640x40") == "yolo11m.pt"
    # 两处都不可信 → 原样返回，守卫按未知模型保守处理
    assert _checkpoint_model({"model": "x/weights/last.pt"}, "probe-640x40") == "last.pt"


def test_train_config_has_resume_default_empty():
    from fire_sight_platform.runtime_config.base import TrainConfig

    assert TrainConfig(dataset="x").resume == ""


# --------------------------------------------------------------------------
# 现场名上报：必须早于 model.train()
#
# 网页端要在「任务还没跑完」时就知道这次训练落到哪个现场。等到训练结束才说，
# 被暂停的任务永远等不到那一刻——用户点了暂停，界面就再也说不出该续哪个现场。
# 所以这两条测的不是「回调被调了」，而是**调用顺序**。
# --------------------------------------------------------------------------


class _FakeYOLO:
    """假的 YOLO：只记下「train 被调用时，on_run 已经报过了吗」。"""

    def __init__(self, model, seen: list, log: list):
        self._seen = seen
        self._log = log

    def add_callback(self, event, handler):  # pragma: no cover - 本组不传 on_epoch
        self._log.append(("add_callback", event))

    def train(self, **kwargs):
        # 这一行就是判据：走到 train 时 on_run 必须已经报过了
        self._log.append(("train", list(self._seen)))
        from types import SimpleNamespace

        return SimpleNamespace(save_dir=kwargs.get("project", ""))


def _fake_yolo_factory(seen: list, log: list):
    class YOLO(_FakeYOLO):
        def __init__(self, model):
            super().__init__(model, seen, log)

    return YOLO


def test_fresh_run_reports_the_experiment_name_before_training(tmp_path, monkeypatch):
    """上报的是**现场目录名**（runs/training 下的那一层），不是归档名。

    设了 experiment 时两者不同，而 resume_run_id 认的是前者——报错了界面就会去
    续一个不存在的现场。
    """
    import fire_sight_platform.training.service as svc
    from fire_sight_platform.runtime_config.base import TrainConfig

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")
    monkeypatch.setattr(svc, "RUNS_DIR", tmp_path / "runs")

    seen: list = []
    log: list = []
    cfg = TrainConfig(dataset="x", epochs=1, experiment="我的实验")

    svc._train_fresh(cfg, None, lambda rid: seen.append(rid), _fake_yolo_factory(seen, log))

    assert seen == ["我的实验"]
    assert log == [("train", ["我的实验"])], "on_run 必须早于 model.train()"


def test_fresh_run_without_experiment_reports_the_generated_run_id(tmp_path, monkeypatch):
    import fire_sight_platform.training.service as svc
    from fire_sight_platform.runtime_config.base import TrainConfig

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")
    monkeypatch.setattr(svc, "RUNS_DIR", tmp_path / "runs")

    seen: list = []
    log: list = []
    cfg = TrainConfig(dataset="D-Fire/raw", model="yolo11s.pt", epochs=1)

    _, run_id, _ = svc._train_fresh(
        cfg, None, lambda rid: seen.append(rid), _fake_yolo_factory(seen, log)
    )

    # 没设 experiment 时两者相同：现场目录就叫这个
    assert seen == [run_id]
    assert log == [("train", [run_id])]


def test_resume_reports_the_checkpoint_scene_before_training(tmp_path, monkeypatch):
    """续训的现场名来自检查点里的 `info["name"]`——就是那个正在被追加的目录。

    报错的话「继续训练」会指向另一个现场，等于在错误的目录上接着跑。
    """
    import types

    from fire_sight_platform.runtime_config.base import TrainConfig
    from fire_sight_platform.training import service as svc

    path = tmp_path / "现场A" / "weights" / "last.pt"
    make_checkpoint(path, done_epochs=10, total=50, stripped=False)

    seen: list = []
    log: list = []

    fake = types.ModuleType("ultralytics")
    fake.YOLO = _fake_yolo_factory(seen, log)
    # 必须换 sys.modules 而不是属性：_train_from_checkpoint 里是函数内 import
    monkeypatch.setitem(sys.modules, "ultralytics", fake)

    _, run_id, _ = svc._train_from_checkpoint(
        TrainConfig(resume=str(path)), None, lambda rid: seen.append(rid)
    )

    assert run_id == "现场A"
    assert seen == ["现场A"]
    assert log == [("train", ["现场A"])], "on_run 必须早于 model.train()"


def test_a_refused_checkpoint_never_reports_a_run_id(tmp_path, monkeypatch):
    """拒绝路径上不能先报现场再抛异常——界面会拿到一个不该出现的「可续」提示。"""
    from fire_sight_platform.runtime_config.base import TrainConfig
    from fire_sight_platform.training.service import _train_from_checkpoint

    path = tmp_path / "weights" / "last.pt"
    make_checkpoint(path, done_epochs=30, total=30, stripped=True)

    seen: list = []
    with pytest.raises(ValueError, match="优化器"):
        _train_from_checkpoint(TrainConfig(resume=str(path)), None, lambda rid: seen.append(rid))

    assert seen == []


def test_a_broken_run_callback_does_not_kill_the_training(caplog):
    """回调里抛异常不该毁掉一次几十分钟的训练。"""
    from fire_sight_platform.training.service import _notify_run

    def boom(_run_id: str) -> None:
        raise RuntimeError("管道断了")

    with caplog.at_level("ERROR"):
        _notify_run(boom, "现场A")  # 不该抛出去

    assert "上报 run_id 失败" in caplog.text
    assert _notify_run(None, "现场A") is None


# --------------------------------------------------------------------------
# 子进程协议：run 事件确实发出去了，且在 done 之前
# --------------------------------------------------------------------------


def test_worker_emits_run_before_epochs_and_done(tmp_path, monkeypatch):
    """父进程只认协议行。worker 少发一条 run，整条「暂停后还能续」的链路就断了。

    `main` 每行都 flush 到 `_PROTOCOL`，所以这里换掉它就能把协议流整条截下来，
    不必起真进程、也不必真加载 ultralytics。
    """
    import io
    from types import SimpleNamespace

    from fire_sight import train_worker
    from fire_sight_platform.training import service as svc

    def fake_train_yolo(config, on_epoch=None, on_run=None):
        if on_run is not None:
            on_run("20261009-170000-D-Fire-train-yolo11n")
        if on_epoch is not None:
            on_epoch({"epoch": 1, "epochs": 2, "metrics": {}, "losses": {}})
        return SimpleNamespace(
            run_id="20261009-170000-D-Fire-train-yolo11n", best_pt="b.pt", save_dir="s"
        )

    monkeypatch.setattr(svc, "train_yolo", fake_train_yolo)

    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text('{"dataset": "demo_fire", "epochs": 2}', encoding="utf-8")

    buf = io.StringIO()
    monkeypatch.setattr(train_worker, "_PROTOCOL", buf)

    assert train_worker.main(["worker", str(cfg_path)]) == 0

    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert [line["type"] for line in lines] == ["start", "run", "epoch", "done"]
    assert lines[1]["run_id"] == "20261009-170000-D-Fire-train-yolo11n"
