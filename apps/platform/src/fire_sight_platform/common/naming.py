"""运行产物命名规则(单一真相)。"""

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize(name: str) -> str:
    """把任意字符串收成安全的文件名片段。"""
    cleaned = _UNSAFE.sub("-", (name or "").strip()).strip("-")
    return cleaned or "unnamed"


def safe_child(root: Path, *parts: str) -> Path:
    """把 parts 拼到 root 下,并保证结果不会逃出 root。

    存在的理由:Windows 上 `Path("a") / "/b"` 会**丢掉 a** 直接变成 `\\b`,
    而 `..` 段在任何平台都会往上爬。凡是「用户输入 → 目录名」的地方
    (产物目录、归档名、训练现场名)都必须过这里,否则一个 `../..` 就能让
    下游的 `shutil.rmtree` 删到工作区外面去。

    Raises:
        ValueError: 拼接结果落在 root 之外。
    """
    base = Path(root).resolve()
    target = base
    for part in parts:
        target = target / part
    resolved = target.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"路径越出 {base}: {'/'.join(parts)} -> {resolved}")
    return resolved


def dataset_stem(name: str) -> str:
    return sanitize(name)


def split_stem(dataset: str, seed: int) -> str:
    return f"{sanitize(dataset)}-seed{seed}"
