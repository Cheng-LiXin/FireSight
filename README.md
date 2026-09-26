# FireSight —— 基于 YOLO 的火灾烟雾智能预警系统

在摄像头视频流中定位并分类【明火 / 烟雾】，输出『类别 + 边界框 + 置信度』。
**核心认知**：算法是可替换的变量，工程链路是稳定的常量。FireSight 把数据接入与
标准化、超参数管理、训练产物归档、推理加速等工程能力一次性沉淀、长期复用。

## 仓库结构(Monorepo + apps/)

```
apps/platform/     → 核心引擎端：数据 + 训练 + 评估 + 推理(fire_sight_platform)
apps/fire-sight/   → 火灾烟雾应用端：场景包 + 监测业务(fire_sight)
frame_source/      → 顶层能力包：可插拔帧输入(摄像头/视频/图片夹/RTSP)
visualization/     → 顶层能力包：检测框美化、中文标签绘制
data/              → 共享数据(raw 只读原始 / processed 冻结划分)
models/            → 共享权重(pretrained 输入 / trained 产物)
runs/              → 每次运行的完整现场
scripts/           → 开发期入口(装包后退回)
tests/             → 占位：跨端测试的家
docs/              → 项目文档(设计 / ADR / ROADMAP)
```

## 命名约定

| 层次 | 值 |
|---|---|
| 产品名 | FireSight |
| 仓库 | `E:\FireSight` |
| 引擎包 | `fire_sight_platform` / `fire-sight-platform` |
| 应用包 | `fire_sight` / `fire-sight` |
| 根标记 | `.firesight-workspace` |

## 快速开始

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows
pip install -e "./apps/platform[dev]"
pip install -e "./apps/fire-sight"
fs-init                                  # 初始化项目核心目录 + 环境快照
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `fs-init` | 初始化项目核心目录 + 环境快照 |

## 设计文档

- `docs/architecture/ADR-001-monorepo.md` —— Monorepo + apps/ 布局决策
