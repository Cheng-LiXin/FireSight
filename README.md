# FireSight —— 基于 YOLO 的火灾烟雾智能预警系统

在摄像头视频流中定位并分类【明火 / 烟雾】，输出「类别 + 边界框 + 置信度」。

**核心认知**：算法是可替换的变量，工程链路是稳定的常量。
FireSight 把数据接入与标准化、超参数管理、训练产物归档、评估对照等工程能力一次性沉淀、长期复用。

## 仓库结构（Monorepo + apps/）

```
apps/platform/     → 核心引擎端：数据 + 训练 + 评估 + 推理(fire_sight_platform)
apps/fire-sight/   → 火灾烟雾应用端：场景包 + 监测业务(fire_sight)
frame_source/      → 顶层能力包：可插拔帧输入(摄像头/视频/图片夹/RTSP)
visualization/     → 顶层能力包：检测框美化、中文标签绘制
data/              → 共享数据(raw 只读原始 / processed 冻结划分)
models/            → 共享权重(pretrained 输入 / trained 产物)
runs/              → 每次运行的完整现场
scripts/           → 开发期入口(装包后退役)
tests/             → 跨端测试
docs/              → 项目文档(设计 / ADR / API / 开发日志)
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate                # Windows
# 或 .venv\Scripts\Activate.ps1

pip install -e "./apps/platform[dev]"
fs-init                                 # 初始化项目目录 + 环境快照
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `fs-init` | 初始化项目核心目录 + 环境快照 |
| `fs-transform` | 原始数据集 → 可训练 YOLO 成品(转换 + 冻结划分) |
| `fs-validate` | 数据质检(只读) + HTML/JSON 报告 |
| `fs-gen-config` | 反射生成自解释的 YAML 运行配置模板 |
| `fs-train` | 训练链路(配置合并 → 训练 → 归档)，终端逐轮打印；支持 `--fraction`/`--cache`/`--workers` |
| `fs-val` | 模型评估 / 多权重对照表 |

## 从数据到训练（照抄即可）

### 1. 放入数据集

```text
data/raw/<数据集名>/
  images/          # .jpg/.png ...
  annotations/     # VOC xml / COCO json / YOLO txt
```

公开火灾数据集建议：D-Fire、BowFire、FLAME、VisiFire；并补充负样本（夕阳、车灯、蒸汽、烟囱白汽）。

### 2. 转换 + 冻结划分

```bash
fs-transform --dataset D-Fire --format pascal_voc --classes flame,smoke --seed 42
```

产出：`data/processed/D-Fire/`（train/val/test + `data.yaml`）。

### 3. 数据体检（可选但推荐）

```bash
fs-validate --dataset D-Fire
```

浏览器打开 `data/processed/D-Fire/reports/quality_report.html`。

### 4. 训练（终端实时打印每轮 loss / mAP）

```bash
# 生成配置模板（一次即可，可改）
fs-gen-config --kind train

# 实验/调参（快）：子集 + 大 batch + 缓存
fs-train --dataset D-Fire_train --model yolo11n.pt --epochs 10 --batch 16 --fraction 0.4 --cache --device 0

# 完整训练（推荐默认）
fs-train --dataset D-Fire_train --model yolo11n.pt --epochs 50 --batch 16 --device 0
```

训练过程每轮在终端输出：

```text
Epoch    GPU_mem   box_loss   cls_loss   dfl_loss  Instances  Size
1/50     ...
     Class  Images  Instances  Box(P  R  mAP50  mAP50-95)
     all    ...
```

结束后自动归档到 `models/trained/<run_id>/`（best.pt + 配置 + 指标）。

### 5. 评估 / 对照

```bash
fs-val --dataset D-Fire --weights models/trained/<run_id>/best.pt

# 两个模型对照
fs-val --dataset D-Fire --weights models/trained/<n>/best.pt models/trained/<s>/best.pt
```

## 推荐默认超参

| 参数 | 值 |
|---|---|
| 模型 | yolo11n.pt（对照 yolo11s.pt） |
| epochs | 50 |
| imgsz | 640 |
| batch | 8 |
| seed | 42 |
| device | 0 (CUDA) |

## 设计文档

- `docs/ROADMAP.md` — S0–S4 路线图
- `docs/tech/` — 技术设计
- `docs/api/` — CLI / 接口文档
- `docs/devlog/` — 开发日志
- `docs/architecture/ADR-001-monorepo.md` — Monorepo 布局决策
