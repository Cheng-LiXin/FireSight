# REST 接口说明

FastAPI 服务，默认监听 `http://127.0.0.1:8000`。启动：`fs-serve`。

生产模式下该服务同时托管打包后的前端（`apps/web/dist/`），因此前端代码中一律使用**相对路径** `/api/...`。

> **契约约定**：本文件是前后端共同契约。修改接口必须同步更新本文件与
> `apps/web/src/api/types.ts`。响应字段的任何改名都属破坏性变更。

交互式文档：服务运行后访问 `/docs`（Swagger UI）或 `/redoc`。

---

## 通用约定

| 项 | 说明 |
|---|---|
| 基础路径 | `/api` |
| 数据格式 | JSON（`Content-Type: application/json`） |
| 时间格式 | ISO 8601 |
| 错误格式 | `{"detail": "<可读的中文说明>"}` |
| 鉴权 | 无。本服务面向单机本地运行，不要直接暴露到公网 |

### 运行时目录

所有缓存、临时文件与过程副产物都落在**运行时目录**（默认 `<工作区>/.runtime/`，
`FIRESIGHT_RUNTIME_DIR` 可覆盖），不写系统盘：

| 子目录 | 内容 |
|---|---|
| `tmp/` | 训练任务配置、子进程 `TEMP`/`TMP`、torch 编译缓存 |
| `ultralytics/` | `YOLO_CONFIG_DIR`，内含钉定过的 `settings.json` 与 `runs_dir`/`weights_dir`/`datasets_dir` |
| `cache/` | matplotlib 字体缓存、`TORCH_HOME`、`HF_HOME` |
| `evaluation/` | `fs-val` / 多权重对比的评估图件（过程副产物，无消费方，故不进 `runs/`） |

服务启动、训练子进程与两条推理路径都会先执行改道。**用户的全局配置
（`%APPDATA%\Ultralytics\settings.json`）不会被项目改写**——若改道未生效，
`pin_ultralytics_paths()` 会拒绝落盘并告警。

### 类别契约

所有涉及类别的字段都遵循同一约定，**顺序即 `class_id`**：

| `id` | `name` | `name_cn` |
|---:|---|---|
| 0 | `smoke` | 烟雾 |
| 1 | `fire` | 明火 |

---

## GET `/api/health`

存活探测。

```json
{ "status": "ok", "spa_built": true }
```

| 字段 | 说明 |
|---|---|
| `spa_built` | 前端是否已构建。为 `false` 时只有接口可用，访问根路径拿不到页面 |

---

## GET `/api/system/status`

环境总览。用于顶栏状态显示与故障排查。

```json
{
  "workspace": {
    "root": "E:\\FireSight",
    "data_dir": "E:\\FireSight\\data",
    "models_dir": "E:\\FireSight\\models",
    "events_db": "E:\\FireSight\\data\\events\\events.db",
    "events_db_exists": false
  },
  "python_version": "3.12.13",
  "gpu": {
    "available": true,
    "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
    "memory_total_mb": 8188,
    "memory_used_mb": 1564,
    "utilization_pct": 5,
    "reason": null
  },
  "gpu_job": { "holder": null, "waiting": [], "busy": false },
  "trained_model_count": 5,
  "active_model": "20260926-190635-D-Fire/train-yolo11n",
  "event_count": 0,
  "classes": [
    { "id": 0, "name": "smoke", "name_cn": "烟雾" },
    { "id": 1, "name": "fire", "name_cn": "明火" }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `gpu.available` | 为 `false` 时其余 GPU 字段为 `null`，失败原因在 `gpu.reason` |
| `gpu_job.holder` | 当前占用显存的任务（`train` / `infer`），空闲为 `null` |
| `gpu_job.waiting` | 排队中的任务。前端据此提示「训练进行中，可排队」 |
| `active_model` | 最新一次归档训练的 `run_id`，无归档时为 `null` |
| `event_count` | 事件库记录数。事件库不存在时为 `0`，**不报错** |

> **GPU 探测方式**：优先调用 `nvidia-smi`（约 50ms）。刻意不 import torch——
> 首次 import 需数秒，会让状态接口慢到不可用。

---

## GET `/api/overview`

仪表盘聚合数据。一次拉取全部所需内容，避免前端并发请求后再拼装。

**查询参数**

| 参数 | 类型 | 默认 | 说明 |
|---|---:|---:|---|
| `days` | int | 30 | 趋势图回溯天数 |

**响应**

```json
{
  "alerts": { "total": 51, "pending": 45, "false_positive_rate": 0.1176 },
  "risk": {
    "score": 0.76,
    "level": "warn",
    "alerts_24h": 4,
    "peak_conf_24h": 0.76,
    "basis": "近 24 小时 4 次告警，峰值置信度 0.76"
  },
  "class_ratio": { "smoke": 32, "fire": 19 },
  "daily": [
    { "date": "2026-08-28", "count": 0, "smoke": 0, "fire": 0, "avg_conf": 0.0 }
  ],
  "model": {
    "run_id": "20260926-190635-D-Fire/train-yolo11n",
    "exists": true,
    "metrics": {
      "dataset": "D-Fire/train",
      "model": "yolo11n.pt",
      "mAP50": "0.7446186143380706",
      "mAP50_95": "0.42897994110357224",
      "precision": "0.7439023759573586",
      "recall": "0.6865913701767612"
    }
  }
}
```

### `risk` —— 风险尺读数

`score` 取**峰值置信度**与**告警量**两者的较大值，取值 `0..1`：

```
volume = min(24 小时内告警数 / 10, 1)
score  = max(24 小时内最高置信度, volume)
```

设计意图：单次高置信度的明火，与持续多次的告警，都应把游标推向高位。
`level` 由 `score` 分档，前端只用它选颜色，**数值以 `score` 为准**：

| `level` | 阈值 |
|---|---|
| `normal` | `score < 0.35` |
| `watch` | `0.35 ≤ score < 0.6` |
| `warn` | `0.6 ≤ score < 0.85` |
| `alert` | `score ≥ 0.85` |

### `daily` —— 每日序列

**序列恒定连续**：从 `days-1` 天前到今天，每天一项，无告警的日子补 0。

> 这一点是刻意的。若只返回有告警的日期，坐标轴会把「空窗」画成正常间隔——
> 那等于对读者说谎。前端据此直接按日期铺满 X 轴。

注意 `count` 为 0 是**真实的零**，而此时的 `avg_conf` 为 **0 但语义是「未定义」**
（当天没有样本，平均置信度无从计算）。前端对此做断点处理而非画成 0，
否则折线会砸到谷底，读起来像「模型置信度为零」。

### `model`

`metrics` 直接映射 `models/trained/<run_id>/metrics.txt` 的键值对，
**值为原始字符串**（如 `"0.7733534279277221"`）。展示层需自行收窄到合适位数。

`exists` 为 `false` 时其余字段为 `null` / 空对象，前端据此显示空态。

### 空态行为

事件库不存在时返回全零结构而非错误——**首次部署就是这个状态**：

```json
{
  "alerts": { "total": 0, "pending": 0, "false_positive_rate": 0.0 },
  "risk": { "score": 0.0, "level": "normal", "alerts_24h": 0, "peak_conf_24h": 0.0, "basis": "近 24 小时无告警" },
  "class_ratio": { "smoke": 0, "fire": 0 },
  "daily": [ /* 仍为 days 项，全部为 0 */ ],
  "model": { "run_id": null, "exists": false, "metrics": {} }
}
```

前端判断「有没有数据」应看 `alerts.total`，**不要**看 `daily` 的长度——
序列永远是满的。

---

## GET `/api/models`

已归档训练列表，新→旧。识别页用它填权重下拉，模型库页后续在此扩展。

```json
[
  {
    "run_id": "20260926-190635-D-Fire/train-yolo11n",
    "mAP50": 0.7446186143380706,
    "precision": 0.7439023759573586,
    "recall": 0.6865913701767612,
    "created": "2026-09-26T19:48:23",
    "is_active": true
  }
]
```

指标缺失或不可解析时为 `null`，**不是 0**——「没测过」和「测得 0 分」不是一回事，
前端应显示「—」。同一时刻至多一项 `is_active`。

---

## GET `/api/infer/sources`

可批量推理的图片目录。前端据此填下拉，**不接收前端拼的任意路径**——
否则这个接口就成了能遍历工作区的文件浏览器。

```json
[
  {
    "key": "D-Fire/train/val",
    "dataset": "D-Fire/train",
    "split": "val",
    "path": "E:\\FireSight\\data\\processed\\D-Fire\\train\\images\\val",
    "image_count": 1722
  }
]
```

以 `data.yaml` 是否存在作为数据集完整性的标志，只列完整的数据集。
数据集名可能自带一层子路径（`--dataset "D-Fire/train"` 的产物落在
`processed/D-Fire/train/`），故扫描最多认两层。

---

## POST `/api/infer/image`

上传单张图像识别。`multipart/form-data`。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `file` | file | 必填 | JPG / PNG / BMP / WebP，上限 20MB |
| `weights` | str | 最新归档 | `run_id`；留空取最新 |
| `conf` | float | 0.25 | 置信度阈值 0–1 |
| `iou` | float | 0.6 | NMS 的 IoU 阈值 0–1 |
| `device` | str | `0` | `0` 或 `cpu`；显卡不可用时自动降级为 CPU |

```json
{
  "detections": [
    { "cls_id": 1, "name": "fire", "name_cn": "明火", "conf": 0.8723,
      "xyxy": [611.1, 433.8, 751.2, 546.7] }
  ],
  "count": 4,
  "classes": ["fire", "smoke"],
  "max_conf": 0.8723,
  "elapsed_ms": 16.3,
  "width": 1280,
  "height": 720,
  "weights": "20260926-190635-D-Fire/train-yolo11n",
  "device": "cpu",
  "annotated": "data:image/jpeg;base64,..."
}
```

`annotated` 是标注图的 JPEG data URL。用 JPEG 而非 PNG：照片类图像 PNG 动辄
数 MB，base64 后更大，单图响应会拖到秒级。

**错误**

| 码 | 场景 |
|---:|---|
| 400 | 上传为空、或无法解码 |
| 404 | `weights` 指定的 run 不存在 |
| 409 | GPU 被训练占用（`detail` 含占用者） |
| 413 | 超过体积上限 |

---

## POST `/api/infer/batch`

对已处理数据集的某个划分批量推理。`application/json`。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---:|---|
| `source_key` | str | 必填 | 取自 `/api/infer/sources` |
| `weights` / `conf` / `iou` / `device` | — | 同上 | |
| `limit` | int | 50 | 1–300 |

```json
{
  "rows": [
    { "file": "AoF04059.jpg", "count": 4, "classes": ["fire", "smoke"],
      "max_conf": 0.8723, "elapsed_ms": 18.1 }
  ],
  "files": 50,
  "hit_files": 31,
  "total_dets": 96,
  "avg_ms": 18.4,
  "class_totals": { "fire": 60, "smoke": 40 },
  "weights": "20260926-190635-D-Fire/train-yolo11n",
  "device": "cpu"
}
```

`source_key` 不存在时返回 404。批量**不返回标注图**——只需统计数字，
渲染标注图在批量规模下是纯浪费。

---

## 训练中心

`run_id` 含斜杠（如 `20260926-190635-D-Fire/train-yolo11n`），走路径参数会被路由
切碎，因此这四个接口**一律用查询参数**，并只接受已发现的 run，不做任意路径拼接。

### GET `/api/train/runs`

全部训练记录，新→旧。`archived` 表示其结果已归档到 `models/trained/`。

```json
[
  {
    "run_id": "20260926-190635-D-Fire/train-yolo11n",
    "dataset": "D-Fire/train",
    "model": "yolo11n.pt",
    "epochs_done": 50,
    "archived": true,
    "mAP50": 0.7446186143380706,
    "mAP50_95": 0.42897994110357224,
    "precision": 0.7439023759573586,
    "recall": 0.6865913701767612,
    "artifact_count": 20,
    "created": "2026-09-26T19:48:23"
  }
]
```

`epochs_done` 来自现场 `results.csv` 的行数，因此**中断的训练也会被列出**，
且轮数就是它实际跑到的地方。未归档的 run（`archived: false`）没有指标，字段为 `null`。

### GET `/api/train/curves?run_id=...`

逐轮曲线，直接来自现场 `results.csv`。

```json
{
  "run_id": "20260926-190635-D-Fire/train-yolo11n",
  "epochs": [1, 2],
  "losses": { "train_box": [1.695, 1.695], "val_cls": [28.769, 27.156] },
  "metrics": { "mAP50": [0.3699, 0.3758], "precision": [0.465, 0.400] },
  "lr": { "pg0": [0.00055, 0.00109] },
  "total_epochs": 2
}
```

各序列**等长于 `epochs`**，缺失值以 `null` 表示。前端应据此断开折线，
而不是画到 0——那会让曲线砸到谷底，读起来像「指标归零」。

> **注意读数方式**：实测某轮的 `val_cls` 在 28.8 与 87.2 之间震荡，而其余五条损失
> 全程在 1.1–2.2。同一根线性轴放不下 58 倍的差距——界面用「每种损失一张小倍数图、
> 各自自适应量程」呈现，不用对数轴掩盖。

### GET `/api/train/artifacts?run_id=...`

现场图件清单。`kind` 供界面分组，避免把一堆文件名平铺出来。

```json
[ { "name": "confusion_matrix_normalized.png", "kind": "confusion", "size": 213740 } ]
```

`kind` 取值：`confusion` / `curves` / `pr` / `labels` / `batch` / `other`。

### GET `/api/train/artifact?run_id=...&name=...`

取单张图件（图片二进制）。`name` **只允许现场目录下的顶层文件名**——
带路径分隔符一律 400，非图片后缀一律 404，避免这个接口变成任意文件读取。

---

### GET `/api/train/limits?imgsz=...&device=...`

本机在给定 `imgsz` 下允许的最大 `batch`。前端据此在表单上提前提示，用户不必先
提交一次、拿到 400 才知道超了。

| 参数 | 默认 | 说明 |
|---|---|---|
| `imgsz` | 480 | 32–2048 |
| `device` | `0` | `0` 或 `cpu` |

```json
{
  "checked": true,
  "total_mb": 8188,
  "px_budget": 16588800,
  "max_batch": 40,
  "reference": "batch=72 × imgsz=480(20260926-190635-D-Fire,mAP@50 0.745)"
}
```

`checked=false` 表示**没做限制**（显式选了 CPU，或 `nvidia-smi` 探测不到显卡）,
此时 `max_batch` 为 `null`。探测失败是环境问题，不该变成训练的拦路石。

预算模型：峰值显存大致正比于 `batch × imgsz²`，比例常数标定自本机唯一跑完且
指标可信的现场（`batch=72 × imgsz=480`、RTX 4060 Laptop 8 GB）。这是启发式而
非精确测量，见 `fire_sight_platform/training/vram.py`。

### POST `/api/train/jobs`

启动一次训练（**子进程**运行，训练崩溃不会带走 API）。

| 字段 | 默认 | 说明 |
|---|---:|---|
| `dataset` | 必填 | 已处理数据集名，如 `D-Fire/train` |
| `model` | `yolo11n.pt` | |
| `epochs` | 50 | 1–1000 |
| `batch` | 16 | |
| `imgsz` | 640 | |
| `device` | `0` | `0` 或 `cpu` |
| `fraction` | 1.0 | 取数据集的比例，>0 且 ≤1。小规模验证很有用 |
| `patience` | 10 | |
| `workers` | 4 | Windows 下建议 0，省去子进程开销 |
| `cache` / `seed` | false / 42 | |

```json
{
  "id": "c954c5781b9d", "dataset": "demo_fire", "epochs": 3, "status": "running",
  "started_at": "2026-09-26T22:11:36", "finished_at": null, "stop_requested_at": null,
  "run_id": null, "error": null, "returncode": null, "event_count": 1,
  "config": { "…回显请求参数…" }
}
```

**错误**：404 数据集不存在；400 `batch × imgsz` 超出本机显存预算（`detail` 里给出
上限与可照做的建议）；409 已有训练在跑（`detail` 里是占用者）。

> 显存校验在起进程**之前**。等 ultralytics 自己报 CUDA OOM，模型已经加载、
> 显存已经吃满、时间已经烧掉——2026-09-27 那次 `batch=48 × imgsz=640` 在 8 GB
> 的 RTX 4060 Laptop 上就是这么废掉的，现已被这条守卫挡下。
>
> 训练全程持有 GPU 全局锁，结束时释放——**包括进程异常退出**。因此训练期间
> 推理接口会返回 409，训练结束后自动恢复。

#### 任务状态 `status`

| 值 | 含义 | 是否占用 GPU |
|---|---|---|
| `running` | 进程在跑 | 是 |
| `stopping` | 已收到停止请求，正在结束进程树 | **是** |
| `done` | 正常结束并归档 | 否 |
| `failed` | 异常结束（崩溃 / 退出码非零 / 协议报了 error） | 否 |
| `stopped` | 用户主动停止，本次不归档 | 否 |

`stopping` **仍占用 GPU**：进程树还没死、显存还没还回来，此时放行新训练就是等着
OOM。因此停止后立刻再启动会得到 409，但 `detail` 会说明是「正在停止，几秒内释放」
而不是含糊的占用提示。实测整个窗口约 0.6 秒。

`stop_requested_at` 非 null 表示这次训练是被主动停止的。被停止的任务**不计为
`failed`**——Windows 上强杀返回退出码 1，若按「退出码非零」判定，界面会显示
「失败 · 退出码 1」，把一次正常停止报成故障。

### GET `/api/train/jobs`

本次服务进程起过的任务，新→旧。**服务重启即清空**——任务是进程内内存状态。
真正的训练产物落在 `runs/` 与 `models/trained/` 下，不依赖这份记录。

### GET `/api/train/jobs/{id}/stream`

SSE，推逐轮指标与日志。

```
event: epoch
data: {"type":"epoch","epoch":1,"epochs":3,
       "metrics":{"precision":0.00693,"recall":1.0,"mAP50":0.09021,"mAP50_95":0.08578},
       "losses":{"val_box":0.44889,"val_cls":2.65732,"val_dfl":0.83776,
                 "train_box":0.86897,"train_cls":3.16089,"train_dfl":0.74454},
       "seq":73}
```

事件类型：`start` / `epoch` / `log` / `done` / `error` / `exit` / `close`。
`close` 带任务最终状态，客户端收到即可收流。

- **指标键与 `/api/train/curves` 完全一致**，因此实时曲线与历史曲线可以复用
  同一套图表配置
- 事件带 `id:` 行（序号）。**断线重连时浏览器发的是 `Last-Event-ID` 头而不是
  查询参数**，服务端两个都认，否则一断线就会从头重放、实时曲线上出现重复点
- 也可用 `?since=N` 主动续传

### POST `/api/train/jobs/{id}/stop`

停止训练。返回 `{"stopped": true|false}`（false 表示任务不在 `running` 状态，
例如已经停过了）。

**结束的是整棵进程树**（Windows 走 `taskkill /F /T`）：只终止直接子进程会留下
DataLoader 的 `workers` 个子进程，它们变成孤儿继续占着显存，下一次训练可能因此
OOM。**本次训练不会归档**，但现场记录留在 `runs/` 下，曲线仍可在训练记录里查看。

该请求**同步**把状态置为 `stopping` 并立刻返回，进程的实际退出由后台完成；
客户端应据 `status` 判断何时可以重新开始训练，而不是假定 stop 返回即已释放。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FIRE_SIGHT_EVENTS_DB` | `<工作区>/data/events/events.db` | 事件库路径，便于测试或多实例 |
| `FIRESIGHT_RUNTIME_DIR` | `<工作区>/.runtime` | 运行时目录（缓存 / 临时文件 / ultralytics 配置 / 评估现场）。改道必须早于 `import ultralytics` |

---

## 待实现接口

以下接口尚未实现，列出以便前端与后端对齐预期：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/events` | 告警事件列表（筛选 / 分页） |
| `POST` | `/api/events/{id}/false-positive` | 标记误报 |
| `POST` | `/api/eval` | 多权重对比评估 |
| `GET` | `/api/models/{run_id}/download` | 下载权重 |
