"""D-Fire / FireSight 类别 id 映射唯一真相。

约定（重训前必须与此一致）：

    class_id 0 -> smoke（烟雾）
    class_id 1 -> fire （明火）

转换（fs-transform）、data.yaml、训练 names、推理显示全部引用本模块，
禁止在别处硬编码 fire/smoke 顺序。
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

# 有序：index 即 class_id
CLASS_NAMES: Tuple[str, ...] = ("smoke", "fire")

NAME_CN: Dict[str, str] = {
    "smoke": "烟雾",
    "fire": "明火",
}

MODEL_ID_TO_NAME: Dict[int, str] = {i: n for i, n in enumerate(CLASS_NAMES)}


def class_to_id() -> Dict[str, int]:
    return {name: i for i, name in enumerate(CLASS_NAMES)}


def display_name(class_id: int, fallback: str = "") -> str:
    return MODEL_ID_TO_NAME.get(int(class_id), fallback or f"class_{class_id}")


def display_label(class_id: int, conf: float | None = None) -> str:
    name = display_name(class_id)
    cn = NAME_CN.get(name, name)
    if conf is None:
        return cn
    return f"{cn} {conf:.2f}"


def names_dict() -> Dict[int, str]:
    """给 YOLO data.yaml 的 names 字段。"""
    return dict(MODEL_ID_TO_NAME)
