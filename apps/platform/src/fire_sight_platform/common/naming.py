"""运行产物命名规则(单一真相)。"""

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize(name: str) -> str:
    """把任意字符串收成安全的文件名片段。"""
    cleaned = _UNSAFE.sub("-", (name or "").strip()).strip("-")
    return cleaned or "unnamed"


def dataset_stem(name: str) -> str:
    return sanitize(name)


def split_stem(dataset: str, seed: int) -> str:
    return f"{sanitize(dataset)}-seed{seed}"
