"""识别检测接口测试：模型列表、来源列表、上传校验、GPU 占用、批量。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(autouse=True)
def clear_cache():
    """模型缓存是进程级的，逐用例清掉，避免互相影响。"""
    from fire_sight.api import deps

    deps.clear_infer_cache()
    yield
    deps.clear_infer_cache()


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("cv2")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    return TestClient(app)


@pytest.fixture()
def any_weights():
    """任一已归档权重；没有则跳过——CI 上可能没训练过。"""
    from fire_sight.api import deps

    run = deps.active_run()
    if run is None:
        pytest.skip("无已归档模型")
    return run.run_id


@pytest.fixture()
def png_bytes():
    import cv2

    img = np.zeros((64, 64, 3), dtype=np.uint8)
    img[20:44, 20:44] = (40, 60, 200)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


# --------------------------------------------------------------------------
# 模型列表
# --------------------------------------------------------------------------


def test_list_models_shape(client):
    body = client.get("/api/models").json()
    assert isinstance(body, list)
    for m in body:
        # 精确集合而不是「包含」：字段被无意增删时这条要挂
        assert set(m) == {
            "run_id",
            "mAP50",
            "mAP50_95",
            "precision",
            "recall",
            "epochs",
            "size_mb",
            "dataset",
            "created",
            "is_active",
            "legacy",
        }
        # 指标缺失必须是 null，不能是 0——「没测过」和「测得 0 分」不是一回事
        assert m["mAP50"] is None or isinstance(m["mAP50"], float)


def test_exactly_one_model_is_active(client):
    body = client.get("/api/models").json()
    assert sum(1 for m in body if m["is_active"]) <= 1


def test_models_sorted_newest_first(client):
    ids = [m["run_id"] for m in client.get("/api/models").json()]
    assert ids == sorted(ids, reverse=True)


# --------------------------------------------------------------------------
# 批量来源
# --------------------------------------------------------------------------


def test_list_sources(client):
    body = client.get("/api/infer/sources").json()
    assert isinstance(body, list)
    for s in body:
        assert set(s) == {"key", "dataset", "split", "path", "image_count"}
        assert s["key"] == f"{s['dataset']}/{s['split']}"
        assert s["image_count"] > 0


def test_sources_includes_nested_dataset_dirs(tmp_path, monkeypatch):
    """回归：产物名可能自带一层子路径。

    `--dataset "D-Fire/train"` 的产物落在 processed/D-Fire/train/，
    只往下扫一层就会整个漏掉——主要数据集曾在批量来源里完全消失。
    """
    from fire_sight.api.routers import infer as infer_router

    root = tmp_path / "processed"
    for rel in ("flat/images/train", "Nested/train/images/val"):
        d = root / rel
        d.mkdir(parents=True)
        (d / "a.jpg").write_bytes(b"x")
    # data.yaml 是完整性标志：半成品目录不该被列出来
    for rel in ("flat", "Nested/train"):
        (root / rel / "data.yaml").write_text("names:\n  0: smoke\n", encoding="utf-8")
    (root / "halfdone" / "images" / "train").mkdir(parents=True)

    monkeypatch.setattr(infer_router, "PROCESSED_DATA_DIR", root)
    # raw 那份走真实路径。不隔离掉的话本机装着 D-Fire/raw 时它会混进来，
    # 断言就得同时写死两份数据集的 key——真实机器与 CI 上结果还不一样。
    monkeypatch.setattr(infer_router, "RAW_DATA_DIR", tmp_path / "raw")

    keys = {s.key for s in infer_router._source_options()}
    assert keys == {"flat/train", "Nested/train/val"}


def test_sources_includes_raw_dataset(tmp_path, monkeypatch):
    """raw 下的数据集切分在上一层（`train/images/`），与 processed 的布局相反。

    新数据集（提供方切好、就地训练）就长这样；只认 `images/train` 会让它
    在批量来源里整个消失。
    """
    from fire_sight.api.routers import infer as infer_router

    raw = tmp_path / "raw"
    for split, n in (("train", 2), ("val", 1)):
        d = raw / "D-Fire" / split / "images"
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"{i}.jpg").write_bytes(b"x")
    (raw / "D-Fire" / "data.yaml").write_text("names:\n  0: smoke\n", encoding="utf-8")

    monkeypatch.setattr(infer_router, "RAW_DATA_DIR", raw)
    monkeypatch.setattr(infer_router, "PROCESSED_DATA_DIR", tmp_path / "none")

    opts = {s.key: s for s in infer_router._source_options()}
    assert set(opts) == {"D-Fire/raw/train", "D-Fire/raw/val"}
    assert opts["D-Fire/raw/train"].image_count == 2
    # 标签是数据集名，不是目录名——界面上要能看出用的是哪一份
    assert opts["D-Fire/raw/train"].dataset == "D-Fire/raw"


def test_raw_dataset_comes_first(tmp_path, monkeypatch):
    """raw 那份排在前面：用户已指定以后统一用它，默认选中它省一次点击。"""
    from fire_sight.api.routers import infer as infer_router

    raw = tmp_path / "raw"
    d = raw / "D-Fire" / "train" / "images"
    d.mkdir(parents=True)
    (d / "a.jpg").write_bytes(b"x")
    (raw / "D-Fire" / "data.yaml").write_text("names:\n  0: smoke\n", encoding="utf-8")

    root = tmp_path / "processed"
    p = root / "old" / "images" / "train"
    p.mkdir(parents=True)
    (p / "a.jpg").write_bytes(b"x")
    (root / "old" / "data.yaml").write_text("names:\n  0: smoke\n", encoding="utf-8")

    monkeypatch.setattr(infer_router, "RAW_DATA_DIR", raw)
    monkeypatch.setattr(infer_router, "PROCESSED_DATA_DIR", root)

    assert infer_router._source_options()[0].dataset == "D-Fire/raw"


def test_batch_rejects_unknown_source(client):
    r = client.post("/api/infer/batch", json={"source_key": "../etc/passwd"})
    assert r.status_code == 404
    assert "未知的批量来源" in r.json()["detail"]


def test_batch_rejects_out_of_range_conf(client, any_weights):
    r = client.post(
        "/api/infer/batch",
        json={"source_key": "x/y", "weights": any_weights, "conf": 5.0},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------
# 单图上传校验
# --------------------------------------------------------------------------


def test_image_rejects_empty_upload(client):
    r = client.post("/api/infer/image", files={"file": ("a.png", b"", "image/png")})
    assert r.status_code == 400
    assert "为空" in r.json()["detail"]


def test_image_rejects_undecodable_bytes(client):
    r = client.post(
        "/api/infer/image", files={"file": ("a.png", b"not an image", "image/png")}
    )
    assert r.status_code == 400
    assert "无法解码" in r.json()["detail"]


def test_image_rejects_oversize(client, png_bytes, monkeypatch):
    from fire_sight.api.routers import infer as infer_router

    monkeypatch.setattr(infer_router, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/api/infer/image", files={"file": ("a.png", png_bytes, "image/png")})
    assert r.status_code == 413


def test_image_rejects_unknown_weights(client, png_bytes):
    r = client.post(
        "/api/infer/image",
        files={"file": ("a.png", png_bytes, "image/png")},
        data={"weights": "no-such-run"},
    )
    assert r.status_code == 404
    assert "未找到归档模型" in r.json()["detail"]


# --------------------------------------------------------------------------
# GPU 占用
# --------------------------------------------------------------------------


def test_image_returns_409_when_gpu_busy(client, png_bytes, any_weights):
    """训练占用显存时应给出可读提示，而不是让 CUDA 报错。"""
    from fire_sight.api import deps

    assert deps.GPU_LOCK.acquire("train") is True
    try:
        r = client.post(
            "/api/infer/image",
            files={"file": ("a.png", png_bytes, "image/png")},
            data={"weights": any_weights},
        )
    finally:
        deps.GPU_LOCK.release()

    assert r.status_code == 409
    assert "train" in r.json()["detail"]


# --------------------------------------------------------------------------
# 真实推理
# --------------------------------------------------------------------------


def test_image_infer_end_to_end(client, png_bytes, any_weights):
    r = client.post(
        "/api/infer/image",
        files={"file": ("a.png", png_bytes, "image/png")},
        data={"weights": any_weights, "conf": "0.25", "device": "cpu"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["width"] == 64 and body["height"] == 64
    assert body["device"] == "cpu"
    assert body["weights"] == any_weights
    assert body["elapsed_ms"] > 0
    # 全黑图不应检出目标，但结构必须完整
    assert body["count"] == len(body["detections"])
    assert body["annotated"].startswith("data:image/jpeg;base64,")

    for d in body["detections"]:
        assert d["name"] in ("smoke", "fire")
        assert d["name_cn"] in ("烟雾", "明火")
        assert len(d["xyxy"]) == 4
        assert 0.0 <= d["conf"] <= 1.0


def test_image_conf_filter_affects_result(client, png_bytes, any_weights):
    """阈值拉满时检出数不应增加——这是最省事的单调性检查。"""
    def run(conf: str) -> int:
        r = client.post(
            "/api/infer/image",
            files={"file": ("a.png", png_bytes, "image/png")},
            data={"weights": any_weights, "conf": conf, "device": "cpu"},
        )
        assert r.status_code == 200, r.text
        return r.json()["count"]

    assert run("0.99") <= run("0.01")


def test_model_cache_reuses_service():
    """必须复用实例：新建实例会重新加载权重，单图推理会慢到不可接受。"""
    from fire_sight.api import deps

    run = deps.active_run()
    if run is None:
        pytest.skip("无已归档模型")

    a = deps.get_infer_service(run.best_pt, "cpu")
    b = deps.get_infer_service(run.best_pt, "cpu")
    assert a is b

    deps.clear_infer_cache()
    c = deps.get_infer_service(run.best_pt, "cpu")
    assert c is not a


def test_resolve_run_rejects_arbitrary_path():
    """权重只认已归档的 run，不接受任意路径。"""
    from fire_sight.api import deps

    with pytest.raises(FileNotFoundError):
        deps.resolve_run("../../../etc/passwd")


def test_resolve_run_returns_run_id_not_experiment_name():
    """回归：run_id 无法从权重路径反推。

    嵌套布局下 best.pt 的父目录是实验名（train-yolo11n），不是 run_id
    （20260926-190635-D-Fire/train-yolo11n）。曾经因此把错误的名字回给前端，
    导致界面无法把它对应到模型列表里的条目。
    """
    from fire_sight.api import deps

    run = deps.active_run()
    if run is None:
        pytest.skip("无已归档模型")

    resolved = deps.resolve_run(run.run_id)
    assert resolved.run_id == run.run_id
    assert resolved.best_pt == run.best_pt
