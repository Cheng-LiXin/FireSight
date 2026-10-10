"""质检报告:JSON/HTML 出口(判断逻辑不在报告里)。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from fire_sight_platform.data_validation.registry import CheckResult, CheckSeverity

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>FireSight 数据质量体检报告</title>
  <style>
    body { font-family: "Microsoft YaHei", sans-serif; margin: 2rem; color: #222; }
    h1 { border-bottom: 2px solid #c0392b; padding-bottom: .4rem; }
    .meta { color: #666; margin-bottom: 1.5rem; }
    table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
    th, td { border: 1px solid #ddd; padding: .55rem .75rem; text-align: left; vertical-align: top; }
    th { background: #f6f6f6; }
    .ERROR { color: #c0392b; font-weight: 700; }
    .WARNING { color: #d35400; font-weight: 700; }
    .INFO { color: #2980b9; }
    .PASS { color: #27ae60; }
    img { max-width: 420px; border: 1px solid #eee; }
    .chart { margin: 1rem 0; }
    ul { margin: .3rem 0 .3rem 1.2rem; }
  </style>
</head>
<body>
  <h1>FireSight 数据质量体检报告</h1>
  <div class="meta">数据集：<strong>{{ dataset }}</strong> · 样本 <strong>{{ n_images }}</strong> · 标签行 <strong>{{ n_labels }}</strong></div>
  {% if chart_b64 %}
  <div class="chart">
    <h2>类别分布</h2>
    <img src="data:image/png;base64,{{ chart_b64 }}" alt="class distribution"/>
  </div>
  {% endif %}
  <h2>检查结果</h2>
  <table>
    <tr><th>检查项</th><th>级别</th><th>说明</th><th>明细</th></tr>
    {% for r in results %}
    <tr>
      <td>{{ r.name }}</td>
      <td class="{{ r.severity }}">{{ r.severity }}</td>
      <td>{{ r.message }}</td>
      <td>{% if r.details %}<ul>{% for d in r.details %}<li>{{ d }}</li>{% endfor %}</ul>{% else %}-{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  <h2>整改建议</h2>
  <ul>
    {% for tip in tips %}<li>{{ tip }}</li>{% else %}<li>暂无 ERROR/WARNING，数据可进入训练。</li>{% endfor %}
  </ul>
</body>
</html>
"""


def _tips(results: List[CheckResult]) -> List[str]:
    tips = []
    for r in results:
        if r.severity == CheckSeverity.ERROR:
            if r.name == "pair_existence":
                tips.append("补齐缺失标签或从清单中移除无标注图像。")
            elif r.name == "label_format":
                tips.append("按 YOLO `class cx cy w h`（0~1 归一化）修复非法标签行。")
            elif r.name == "split_uniqueness":
                tips.append("重新划分并物化，确保同一 stem 只出现在一个 split。")
            elif r.name == "class_balance":
                # 「整个类别一个框都没有」是 ERROR（见 class_balance 的注释）：
                # 训练出来的是一个不会识别该类的模型，比比例失衡严重得多。
                tips.append("有类别没有任何标注框：先确认该类别是否本该出现，再补样本或从 names 里去掉它。")
            elif r.name == "snapshot":
                tips.append("数据集 yaml 没能解析：检查 path/train/val/test 的取值与类型。")
        if r.severity == CheckSeverity.WARNING:
            if r.name == "class_balance":
                tips.append("补充稀有类别样本，或对少数类做增强（注意负样本）。")
            if r.name == "split_presence":
                tips.append("保证 val 划分非空；样本过少时调整划分比例。")
    return list(dict.fromkeys(tips))


def _class_chart_b64(results: List[CheckResult]) -> str:
    try:
        import base64
        import io

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return ""

    balance = next((r for r in results if r.name == "class_balance"), None)
    if balance is None or not balance.details:
        return ""
    names, counts = [], []
    for item in balance.details:
        if ":" not in item:
            continue
        k, v = item.split(":", 1)
        names.append(k.strip())
        try:
            counts.append(int(v.strip()))
        except ValueError:
            continue
    if not names:
        return ""
    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.bar(names, counts, color="#c0392b")
    ax.set_title("Class Distribution")
    ax.set_ylabel("boxes")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_html_report(
    results: List[CheckResult],
    dataset: str,
    n_images: int,
    n_labels: int,
    out_path: Path,
) -> Path:
    from jinja2 import Template

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = Template(HTML_TEMPLATE).render(
        dataset=dataset,
        n_images=n_images,
        n_labels=n_labels,
        results=results,
        tips=_tips(results),
        chart_b64=_class_chart_b64(results),
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path


def write_json_report(results: List[CheckResult], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) for r in results]
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
