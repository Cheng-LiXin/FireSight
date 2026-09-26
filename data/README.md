# data/ —— 共享数据

- `raw/`：原始数据，**只读，绝不就地修改**
- `processed/`：由 raw 生成、含固定种子冻结 train/val/test 划分的数据集

数据集目录约定：
```
data/raw/<数据集名>/
  images/
  annotations/
```
