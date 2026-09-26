"""跨模块共享的纯定义。

新增规则:值属于"两个以上子系统都要引用的业务决策"才进这里;子系统私有默认值留在子系统。
"""

from typing import Tuple


class AnnotationFormat:
    """标注格式标识(注册表键、CLI choices、转换路由共用)。"""

    PASCAL_VOC = "pascal_voc"
    COCO = "coco"
    YOLO = "yolo"

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return (cls.PASCAL_VOC, cls.COCO, cls.YOLO)


class Task:
    """任务类型:检测/分割。"""

    DETECT = "detect"
    SEGMENT = "segment"

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return (cls.DETECT, cls.SEGMENT)


DEFAULT_RANDOM_STATE: int = 42
DEFAULT_TRAIN_RATE: float = 0.8
DEFAULT_VAL_RATE: float = 0.1
RATE_EPSILON: float = 1e-6


class SplitStrategy:
    """划分策略名。"""

    RANDOM = "random"
    STRATIFIED_MULTILABEL = "stratified_multilabel"

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return (cls.RANDOM, cls.STRATIFIED_MULTILABEL)


DEFAULT_SPLIT_STRATEGY: str = SplitStrategy.RANDOM

IMAGE_EXTENSIONS: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


class FireSmokeClass:
    """火灾烟雾双类。顺序即 class_id：0=smoke，1=fire（与 scene.class_map 一致）。"""

    SMOKE = "smoke"
    FIRE = "fire"

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        """有序类名，index == class_id。"""
        return (cls.SMOKE, cls.FIRE)


DEFAULT_FIRE_SMOKE_CLASSES: Tuple[str, ...] = FireSmokeClass.all()
