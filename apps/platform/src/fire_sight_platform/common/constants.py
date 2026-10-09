"""跨模块共享的纯定义。

新增规则:值属于"两个以上子系统都要引用的业务决策"才进这里;子系统私有默认值留在子系统。
"""

from typing import Sequence, Tuple


class AnnotationFormat:
    """标注格式标识(注册表键、CLI choices、转换路由共用)。"""

    PASCAL_VOC = "pascal_voc"
    COCO = "coco"
    YOLO = "yolo"

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return (cls.PASCAL_VOC, cls.COCO, cls.YOLO)


DEFAULT_RANDOM_STATE: int = 42
DEFAULT_TRAIN_RATE: float = 0.8
DEFAULT_VAL_RATE: float = 0.1
RATE_EPSILON: float = 1e-6


class SplitStrategy:
    """划分策略名。

    只列**已实现**的策略：注册表里的键就是这里的取值。曾经写过一个
    `stratified_multilabel`，但从未实现，文档与常量因此对外宣称了一个
    并不存在的策略（2026-10-09 代码审查 R21），已删除。
    """

    RANDOM = "random"


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


class ClassOrderError(ValueError):
    """类别顺序不符合全链路契约。"""


def assert_canonical_class_order(class_names: Sequence[str]) -> None:
    """校验类别顺序是否为契约顺序，不是则拒绝执行。

    顺序即 class_id，且被全链路依赖——包括前端的类别显示与配色。
    放行任何一种别的顺序，都可能让「明火」和「烟雾」整体互换。

    对 VOC / COCO 输入尤其危险：转换器按**名字**查表，错误顺序会直接把
    数字标签写反，而 data.yaml 仍按契约声明，于是模型照常训练、指标照常
    好看，事后几乎无法察觉。YOLO 输入因为原样透传数字 id 反而不会中招，
    这使得错误可能在换数据集时才突然暴露。
    """
    expected = list(DEFAULT_FIRE_SMOKE_CLASSES)
    got = [str(n).strip() for n in class_names]
    if got == expected:
        return
    raise ClassOrderError(
        f"类别顺序必须严格为 {expected}（顺序即 class_id），收到 {got}。\n"
        f"  正确：--classes {'/'.join(expected)}      "
        f"即 class_id 0={expected[0]}、1={expected[1]}\n"
        f"  若你的数据集标注顺序与此不同，先按名字转换，不要靠调换 --classes 迁就。\n"
        f"  契约唯一真相：fire_sight/scene/class_map.py"
    )
