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

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FIRE_SIGHT_EVENTS_DB` | `<工作区>/data/events/events.db` | 事件库路径，便于测试或多实例 |

---

## 待实现接口

以下接口尚未实现，列出以便前端与后端对齐预期：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/infer/image` | 上传图像识别，返回检测框与标注图 |
| `POST` | `/api/infer/batch` | 目录批量推理 |
| `GET` | `/api/train/runs` | 历史训练列表与指标 |
| `GET` | `/api/train/runs/{id}/curves` | 训练曲线数据（解析 `results.csv`） |
| `POST` | `/api/train/jobs` | 启动训练任务 |
| `GET` | `/api/train/jobs/{id}/stream` | 训练实时日志与指标（SSE） |
| `GET` | `/api/events` | 告警事件列表（筛选 / 分页） |
| `POST` | `/api/events/{id}/false-positive` | 标记误报 |
| `GET` | `/api/models` | 归档权重列表 |
| `POST` | `/api/eval` | 多权重对比评估 |
