# FireSight 训练权重与参数导出（供其他系统复现设置）

- **导出日期**：2026-09-28
- **来源系统**：FireSight v1.9.0（`E:\FireSight`）
- **导出对象**：当前生效模型 `20260928-165715-D-Fire-raw-yolo11s`
- **用途**：在另一套系统中设置同等的训练/推理参数

---

## 1. 权重文件

| 项 | 值 |
|---|---|
| 权重文件 | `models/trained/20260928-165715-D-Fire-raw-yolo11s/best.pt` |
| 文件大小 | 18.27 MB |
| 架构 | YOLO11s（detect） |
| 参数量 | **9,428,566** |
| 层数 / 计算量 | 181 层 / 21.7 GFLOPs |
| 类别数 nc | 2 |
| 类别名 | `{0: smoke, 1: fire}` |
| 输入尺寸 imgsz | 640×640 |

> 注意：这是**训练产物**（在 D-Fire 自定义数据上微调过的完整模型），不是 COCO 预训练起点。预训练起点 `yolo11s.pt`（18.40 MB）在 `E:\FireSight\.runtime\ultralytics\weights\`，仅供复现训练时使用。

## 2. 类别契约（必须一致）

```
class_id 0 -> smoke （烟雾）
class_id 1 -> fire  （明火）
```

单一真相在 `apps/fire-sight/src/fire_sight/scene/class_map.py`。data.yaml、训练 names、推理显示全部引用它。**如果另一套系统打乱这个顺序，所有指标和告警逻辑都会错位。**

## 3. 训练参数（应用层）

来源：`models/trained/20260928-165715-D-Fire-raw-yolo11s/train_config.yaml`（文件名 .yaml，内容为 JSON）

| 参数 | 值 | 说明 |
|---|---|---|
| dataset | `D-Fire/raw` | 见第 4 节 |
| model | `yolo11s.pt` | COCO 预训练起点 |
| epochs | 100 | 实跑 82 轮早停（patience=10） |
| imgsz | 640 | |
| batch | 20 | 8 GB 显存的实际上限（640px 下实测峰值 ~7.2 GB） |
| lr0 | 0.01 | |
| patience | 10 | 早停耐心 |
| seed | 42 | |
| device | 0 | RTX 4060 Laptop 8 GB |
| workers | 4 | |
| fraction | 1.0 | 全量数据 |
| cache | false | |
| resume | "" | 非续训 |

## 4. 数据集

| 项 | 值 |
|---|---|
| 位置 | `E:\FireSight\data\raw\D-Fire` |
| 划分 | 提供方给定（**非本项目划分**），train / val / test 三目录，各含 `images/` + `labels/` |
| train | 14122 张 |
| val | 3099 张 |
| test | 4306 张 |
| 标注格式 | YOLO txt（5 列：class_id cx cy w h，归一化） |
| 纯背景图占比 | ≈46%（空标签文件，YOLO 正常处理） |

data.yaml 要点（另一系统需要等价物）：

```yaml
train: <root>/train/images
val:   <root>/val/images
test:  <root>/test/images
names:
  0: smoke
  1: fire
```

## 5. 训练结果（val = 3099 张）

| 指标 | 值 |
|---|---|
| mAP@50 | **0.79226** |
| mAP@50-95 | **0.47068** |
| 精确率 P | 0.78912 |
| 召回率 R | 0.73300 |
| 实跑轮数 | 82（epoch 62 达 mAP@50 峰值 0.79391，62–82 为平台期） |
| 每轮耗时 | ≈256 s（RTX 4060 Laptop，batch 20 / 640px） |
| 总耗时 | ≈4.2 h |

⚠️ 口径提醒：mAP 用 conf=0.001 / iou=0.7 计算（ultralytics 验证默认），与部署阈值无关。

## 6. 推理/部署参数（另一系统最需要的部分）

来源：`apps/fire-sight/src/fire_sight/api/routers/infer.py`、`apps/platform/src/fire_sight_platform/inference/service.py`

| 参数 | 默认值 | 说明 |
|---|---|---|
| conf（置信度阈值） | **0.25** | 部署口径；与 mAP 计算的 0.001 完全不同 |
| iou（NMS 阈值） | **0.6** | 部署口径；训练期验证用 0.7 |
| 多帧确认 confirm_frames | **3** | 连续 3 帧都检出同一类别才确认（抗单帧误检） |
| 告警冷却 cooldown_sec | **30.0** | 按类别冷却：任一类别距上次告警 ≥30 s 才再报 |
| 设备 | GPU 0 | |

告警判定链：`检测(conf≥0.25) → 连续 3 帧确认 → 类别冷却 30 s → 告警事件`。

## 7. 评估口径（如需对比指标）

| 参数 | 值 | 说明 |
|---|---|---|
| conf | 0.001 | mAP 口径，走完整 PR 曲线 |
| iou | 0.7 | 与训练期验证一致 |
| split | val（选模型）/ test（最终汇报） | 本项目 test 从未参与任何决策 |

若另一套系统用 conf=0.25 算 mAP，数字会显著偏低（本项目实测同一个权重 0.7052 → 0.5831），**不可与上表直接比较**。

## 8. 环境版本

| 组件 | 版本 |
|---|---|
| ultralytics | 8.4.163 |
| torch | 2.14.0+cu126 |
| torchvision | 0.29.0+cu126 |
| GPU | RTX 4060 Laptop 8 GB |
| Python venv | 项目内 `.venv`（Windows） |

## 9. 已知事项（迁移前必读）

1. **类别顺序是硬约束**：`0=smoke, 1=fire`。推理结果解析、告警文案、事件筛选都建立在这个顺序上。
2. **本权重训练数据含大量背景图（约 46%）**：如目标系统按「每张图必须有框」校验标注会误报。
3. **多帧确认是产品行为的一部分**：单独用模型不加 3 帧确认，误检率会高于本系统的实际表现。
4. **告警冷却按类别独立计时**：smoke 与 fire 各自 30 s。
5. 旧归档（`20260926/20260927-*` 五个）是用旧数据训的，指标虚高（泄漏 +0.073 mAP@50），**不要**拿它们的参数当基准。
