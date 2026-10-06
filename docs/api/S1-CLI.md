# S1 接口文档 —— CLI

## fs-init（S0）

初始化核心目录 + 环境快照。

```bash
fs-init
```

## fs-transform

原始数据 -> 可训练 YOLO 成品（转换 + 冻结划分）。

```bash
fs-transform --dataset demo_fire --format yolo --classes flame,smoke --seed 42
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dataset` | 必填 | `data/raw/<dataset>` 名称 |
| `--format` | `yolo` | `pascal_voc` / `coco` / `yolo` |
| `--classes` | `flame,smoke` | 类别名，逗号分隔 |
| `--seed` | `42` | 划分种子 |
| `--train-rate` | `0.8` | 训练比例 |
| `--val-rate` | `0.1` | 验证比例（test = 1 - train - val） |
| `--split` | `random` | 划分策略 |

**输出**：`data/processed/<dataset>/` 下 `images|labels/{train,val,test}`、`data.yaml`、`transform_stats.json`。

**退出码**：0 成功，1 失败。

## fs-validate

只读质检 + 报告。

```bash
fs-validate --dataset demo_fire
fs-validate --dataset "D-Fire/raw"                  # 提供方已切好划分的原始数据集
fs-validate --dataset data/processed/demo_fire/data.yaml --out-dir ./reports
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dataset` | 必填 | `processed/<dataset>`、`D-Fire/raw` 或 `data.yaml` 路径 |
| `--out-dir` | 产物为 `<processed>/reports`，raw 为 `.runtime/quality/<name>/` | 报告输出目录 |

**检查项**：`pair_existence`、`label_format`、`split_uniqueness`、`class_balance`、`split_presence`。

**标签布局**：两种都认 —— 转换产物的 `<root>/labels/<split>/`，与提供方划分的
`<split>/labels/`（与 `<split>/images/` 平级）。两个都找不到时报「标签目录不存在」，
而不是「缺失标签 100%」——后者会让人去逐张补标签，而真正的问题是布局不对。

**输出**：`quality_report.html`、`quality_report.json`。

**退出码**：0 无 ERROR，1 存在 ERROR。

## Python API（摘要）

```python
from fire_sight_platform.data_pipeline.orchestrator import run_transform
from fire_sight_platform.data_validation.service import validate_dataset

result = run_transform("demo_fire", source_format="yolo", seed=42)
results = validate_dataset(result.yaml_path)
```
