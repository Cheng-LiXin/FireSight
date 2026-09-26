"""配置加载器:外部数据 → dict(不做合并)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


class YAMLLoader:
    def load(self, path: Path) -> Dict[str, Any]:
        path = Path(path)
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"YAML 根节点必须是映射: {path}")
        return _drop_none(data)


class CLILoader:
    def load(self, overrides: Dict[str, Any]) -> Dict[str, Any]:
        return _drop_none(overrides or {})


def _drop_none(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def load_all_sources(
    yaml_path: Path | None = None,
    cli_overrides: Dict[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """返回 {"yaml": {...}, "cli": {...}}。"""
    return {
        "yaml": YAMLLoader().load(yaml_path) if yaml_path else {},
        "cli": CLILoader().load(cli_overrides or {}),
    }
