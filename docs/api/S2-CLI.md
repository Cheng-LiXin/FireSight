# S2 接口文档 —— CLI

## fs-gen-config
fs-gen-config --kind train --out apps/platform/configs/runtime/train.yaml

## fs-train
fs-train --dataset demo_fire --model yolo11n.pt --epochs 50 --batch 8 --device 0

参数：--dataset --model --epochs --imgsz --batch --lr0 --patience --device --config --experiment

## fs-val
fs-val --dataset demo_fire --weights path/to/best.pt
fs-val --dataset demo_fire --weights a.pt b.pt  # 对照

## Python API
from fire_sight_platform.training.service import train_yolo
from fire_sight_platform.evaluation.service import evaluate_weights, compare_runs
