"""运行配置基类:字段 + 来源标注。"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

ConfigSource = Literal["default", "yaml", "cli"]


class ConfigMetadata(BaseModel):
    source: ConfigSource = "default"


class BaseConfig(BaseModel):
    """所有运行配置的公共字段。"""

    dataset: str = Field(default="demo_fire", description="processed/<dataset> 名称")
    seed: int = Field(default=42, description="随机种子")
    device: str = Field(default="0", description="cuda 设备号或 cpu")

    model_config = {"protected_namespaces": ()}

    def sources_summary(self) -> Dict[str, str]:
        """由子类/合并器填充;基类给空表。"""
        return {}


class TrainConfig(BaseConfig):
    """YOLO 训练配置(S2 推荐默认值)。"""

    model: str = Field(default="yolo11n.pt", description="预训练权重名或路径")
    epochs: int = Field(default=50, ge=1)
    imgsz: int = Field(default=640, ge=32)
    batch: int = Field(default=8, ge=1)
    lr0: float = Field(default=0.01, gt=0)
    patience: int = Field(default=10, ge=0)
    workers: int = Field(default=8, ge=0, description="DataLoader 进程数")
    fraction: float = Field(default=1.0, gt=0, le=1.0, description="仅用数据集该比例样本(实验加速)")
    cache: bool = Field(default=False, description="图像缓存进内存加速后续 epoch")
    project_name: str = Field(default="fire-sight", description="runs 子目录名")
    experiment: Optional[str] = Field(default=None, description="实验名,缺省自动生成")


class ValConfig(BaseConfig):
    """YOLO 评估配置。

    conf / iou 是 **mAP 计算口径**，不是部署阈值——部署阈值属于 InferConfig。

    这里必须与 ultralytics 自己算 mAP 时用的值一致（conf=0.001 走完整 PR 曲线、
    iou=0.7），否则 fs-val 给出的数字与归档里的 metrics.txt 不可比。实测踩到过：
    同一个权重，metrics.txt 记 mAP50 = 0.7052，而 fs-val 用 conf=0.25 只报 0.5831
    —— 差 0.12 全部来自阈值，不是模型差异。
    """

    weights: str = Field(default="", description="待评估权重路径")
    imgsz: int = Field(default=640, ge=32)
    batch: int = Field(default=8, ge=1)
    conf: float = Field(default=0.001, ge=0, le=1, description="mAP 口径，勿当部署阈值")
    iou: float = Field(default=0.7, ge=0, le=1, description="NMS 口径，与训练期验证一致")


class InferConfig(BaseConfig):
    """推理配置(S3 用,先占位)。"""

    weights: str = Field(default="")
    source: str = Field(default="0")
    conf: float = Field(default=0.25, ge=0, le=1)
