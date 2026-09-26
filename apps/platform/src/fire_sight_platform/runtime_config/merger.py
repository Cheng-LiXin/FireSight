"""三源合并 + 来源溯源。"""

from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import BaseModel

from fire_sight_platform.runtime_config.base import BaseConfig


class ConfigMerger:
    def __init__(self, config_cls: Type[BaseConfig]):
        self.config_cls = config_cls

    def merge(
        self,
        yaml_data: Dict[str, Any] | None = None,
        cli_data: Dict[str, Any] | None = None,
    ) -> tuple[BaseConfig, Dict[str, str]]:
        """合并 DEFAULT → YAML → CLI,返回 (config, 来源映射)。"""
        probe = self.config_cls.model_construct()
        defaults = {name: getattr(probe, name) for name in self.config_cls.model_fields}
        sources: Dict[str, str] = {k: "default" for k in defaults}

        merged = dict(defaults)
        for src_name, data in (("yaml", yaml_data or {}), ("cli", cli_data or {})):
            for key, value in data.items():
                if key not in merged:
                    continue  # 未知键忽略,避免静默污染
                merged[key] = value
                sources[key] = src_name

        config = self.config_cls.model_validate(merged)
        return config, sources
