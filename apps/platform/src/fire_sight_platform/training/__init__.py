"""training: 训练编排、归档、指标。"""

from fire_sight_platform.training.service import TrainResult, train_yolo

__all__ = ["train_yolo", "TrainResult"]
