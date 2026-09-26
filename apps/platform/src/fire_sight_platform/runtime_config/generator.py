"""配置模板生成器:从 Pydantic 字段反射出带注释 YAML。"""

from __future__ import annotations

import argparse
import sys
from typing import Type

from pydantic import BaseModel

from fire_sight_platform.common.constants import FireSmokeClass
from fire_sight_platform.common.paths import CONFIGS_DIR
from fire_sight_platform.runtime_config.base import TrainConfig, ValConfig


def render_template(config_cls: Type[BaseModel]) -> str:
    lines = [
        "# FireSight 运行配置模板(由 fs-gen-config 生成)",
        "# 优先级: DEFAULT < 本 YAML < CLI",
        "",
    ]
    schema = config_cls.model_json_schema()
    props = schema.get("properties", {})
    for name, meta in props.items():
        desc = meta.get("description", "")
        default = meta.get("default", None)
        type_name = meta.get("type", "any")
        if desc:
            lines.append(f"# {desc} ({type_name})")
        else:
            lines.append(f"# {type_name}")
        rendered = "null" if default is None else default
        lines.append(f"{name}: {rendered}")
        lines.append("")
    lines.append(f"# 火灾烟雾类别参考: {', '.join(FireSmokeClass.all())}")
    lines.append("")
    return "\n".join(lines)


def generate(kind: str, out_path=None):
    mapping = {"train": TrainConfig, "val": ValConfig}
    if kind not in mapping:
        raise ValueError(f"未知配置类型: {kind}")
    text = render_template(mapping[kind])
    if out_path is None:
        out_path = CONFIGS_DIR / "runtime" / f"{kind}.yaml"
    from pathlib import Path

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="fs-gen-config")
    ap.add_argument("--kind", default="train", choices=["train", "val"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    path = generate(args.kind, args.out)
    print(f"配置模板已生成: {path}")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
