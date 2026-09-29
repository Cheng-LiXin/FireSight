# 大模型火情分析与防火建议报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在值班控制台里对选定时间段的告警数据生成可归档、可下载的 Markdown 报告（火情态势 + 分场所防火建议 + 处置优先级）。

**Architecture:** 统计摘要（不喂原始事件）→ 可配置 LLM 通道生成正文 → Markdown 落盘归档；生成走后台任务 + 轮询（与 `eval_jobs.py` 同模式，生成要几十秒，同步 POST 会超时）。LLM 通道是两个适配器实现的同一个接口：OpenAI 兼容（DeepSeek/SiliconFlow/custom）与 Claude 官方 SDK。

**Tech Stack:** Python 3.12 · FastAPI · Pydantic · stdlib `urllib`（OpenAI 兼容通道）· `anthropic` SDK（Claude 通道）· Vue 3 + TypeScript + `marked` · pytest + Playwright

**Spec:** `docs/superpowers/specs/2026-09-29-llm-fire-report-design.md`

## Global Constraints

- 模型通道默认 `deepseek`（`FIRESIGHT_LLM_PROVIDER`），可切 `siliconflow` / `claude` / `custom`
- Claude 通道走官方 `anthropic` SDK、模型 `claude-opus-5-5`，**不走 OpenAI 兼容层**
- Prompt 输出 Markdown 固定五节：`## 火情态势` / `## 时段与场所规律` / `## 风险点` / `## 防火建议` / `## 处置优先级`
- 区间内无事件 → 拒绝生成（POST 400），不拿空数据花模型的钱
- 未配置模型通道 → POST 400 并给配置说明，不让注定失败的任务白跑
- 归档到 `data/reports/<YYYYMMDD-HHMMSS>-<id>.md` + 同名 `.json`
- 生成进度用离散阶段标签 `聚合数据` / `模型生成中` / `归档中`，**不做假百分比**
- e2e 真调模型的用例只在 `FIRESIGHT_LLM_API_KEY` 存在时跑（`test.skip` 否则跳过）
- 界面文案中文；视觉沿用现有令牌（`--radius` / `--shadow-1` / `--dur-1`），不引入新彩色
- 非目标：定时生成、邮件推送、对话追问、RAG、多语言

---

### Task 1: LLM 通道适配器

**Files:**
- Create: `apps/fire-sight/src/fire_sight/services/llm.py`
- Modify: `apps/fire-sight/src/fire_sight/services/__init__.py`
- Modify: `apps/fire-sight/pyproject.toml`（加 `anthropic` 依赖）
- Test: `tests/test_26_llm.py`

**Interfaces:**
- Consumes: 无（纯新模块）
- Produces（后续任务依赖的精确签名）:
  - `class LlmNotConfigured(RuntimeError)`
  - `class LlmError(RuntimeError)`
  - `@dataclass(frozen=True) class LlmSettings: provider: str; base_url: str; api_key: str; model: str`
  - `resolve_settings() -> LlmSettings`（缺 key 抛 `LlmNotConfigured`）
  - `class OpenAICompatChat: __init__(self, settings: LlmSettings, timeout: float = 120.0)`；`chat(self, system: str, user: str, *, max_tokens: int = 4000) -> str`
  - `class AnthropicChat: __init__(self, settings: LlmSettings)`；`chat(...)` 同上
  - `resolve_client() -> OpenAICompatChat | AnthropicChat`

- [ ] **Step 1: 写失败测试（配置解析）**

创建 `tests/test_26_llm.py`：

```python
"""LLM 通道：配置解析、OpenAI 兼容适配器、Claude 适配器。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _clear_llm_env(monkeypatch):
    for name in (
        "FIRESIGHT_LLM_PROVIDER",
        "FIRESIGHT_LLM_API_KEY",
        "FIRESIGHT_LLM_BASE_URL",
        "FIRESIGHT_LLM_MODEL",
        "DEEPSEEK_API_KEY",
        "SILICONFLOW_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_resolve_settings_defaults_to_deepseek(monkeypatch):
    from fire_sight.services.llm import resolve_settings

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    s = resolve_settings()
    assert s.provider == "deepseek"
    assert s.api_key == "sk-test"
    assert "deepseek" in s.base_url
    assert s.model == "deepseek-chat"


def test_resolve_settings_missing_key_raises(monkeypatch):
    from fire_sight.services.llm import LlmNotConfigured, resolve_settings

    _clear_llm_env(monkeypatch)
    with pytest.raises(LlmNotConfigured) as exc:
        resolve_settings()
    # 报错要能直接贴进界面：说清设哪个变量
    assert "FIRESIGHT_LLM_API_KEY" in str(exc.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_26_llm.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'fire_sight.services.llm'`）

- [ ] **Step 3: 写配置解析的最小实现**

创建 `apps/fire-sight/src/fire_sight/services/llm.py`：

```python
"""可配置的大模型通道：DeepSeek / SiliconFlow / Claude / 自定义。

为什么不绑死一家：现场换模型只该改环境变量，不该改代码。两个适配器实现同一个
`chat(system, user) -> str` 接口——OpenAI 兼容（DeepSeek、SiliconFlow、自定义）
与 Claude 官方 SDK（后者按 Anthropic 规范写，不走兼容层）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class LlmNotConfigured(RuntimeError):
    """没配模型通道。要能直接贴进界面，所以信息里带变量名。"""


class LlmError(RuntimeError):
    """调用失败（网络、限流、服务端错误）。信息给中文，原因带上原始状态。"""


# provider -> (默认 base_url, 默认 model, 可用的 key 环境变量，按优先级)
_PRESETS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat", ["FIRESIGHT_LLM_API_KEY", "DEEPSEEK_API_KEY"]),
    "siliconflow": (
        "https://api.siliconflow.cn/v1",
        "Qwen/Qwen3-32B",
        ["FIRESIGHT_LLM_API_KEY", "SILICONFLOW_API_KEY"],
    ),
    "claude": ("https://api.anthropic.com", "claude-opus-5-5", ["FIRESIGHT_LLM_API_KEY", "ANTHROPIC_API_KEY"]),
    "custom": ("", "", ["FIRESIGHT_LLM_API_KEY"]),
}


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    base_url: str
    api_key: str
    model: str


def resolve_settings() -> LlmSettings:
    """按环境变量解析通道配置。缺 key 抛 LlmNotConfigured，不做静默降级。"""
    provider = (os.environ.get("FIRESIGHT_LLM_PROVIDER") or "deepseek").strip().lower()
    if provider not in _PRESETS:
        raise LlmNotConfigured(
            f"未知的模型通道「{provider}」，可选：{', '.join(sorted(_PRESETS))}"
        )
    base, model, key_vars = _PRESETS[provider]
    api_key = ""
    for name in key_vars:
        api_key = (os.environ.get(name) or "").strip()
        if api_key:
            break
    if not api_key:
        raise LlmNotConfigured(
            "未配置模型通道：请设置 FIRESIGHT_LLM_API_KEY"
            f"（或 {key_vars[-1]}）后重启服务"
        )
    base_url = (os.environ.get("FIRESIGHT_LLM_BASE_URL") or base).rstrip("/")
    model_name = (os.environ.get("FIRESIGHT_LLM_MODEL") or model).strip()
    if provider == "custom" and (not base_url or not model_name):
        raise LlmNotConfigured("自定义通道需要同时设置 FIRESIGHT_LLM_BASE_URL 与 FIRESIGHT_LLM_MODEL")
    return LlmSettings(provider=provider, base_url=base_url, api_key=api_key, model=model_name)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_26_llm.py -q`
Expected: 2 passed

- [ ] **Step 5: 写失败测试（OpenAI 兼容适配器）**

追加到 `tests/test_26_llm.py`：

```python
class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_openai_compat_chat_posts_and_returns_text(monkeypatch):
    from fire_sight.services import llm

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse({"choices": [{"message": {"content": "# 报告正文"}}]})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)

    client = llm.OpenAICompatChat(
        llm.LlmSettings("deepseek", "https://api.deepseek.com", "sk-x", "deepseek-chat")
    )
    out = client.chat("你是专家", "分析这些数据")

    assert out == "# 报告正文"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "你是专家"}
    assert captured["body"]["messages"][1]["content"] == "分析这些数据"


def test_openai_compat_chat_maps_http_errors(monkeypatch):
    from fire_sight.services import llm

    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    client = llm.OpenAICompatChat(
        llm.LlmSettings("deepseek", "https://api.deepseek.com", "sk-x", "deepseek-chat")
    )

    with pytest.raises(llm.LlmError) as exc:
        client.chat("s", "u")
    assert "限流" in str(exc.value)
```

（测试顶部需要 `import urllib.error`。）

- [ ] **Step 6: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_26_llm.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'urllib'` 或 `OpenAICompatChat` 未定义）

- [ ] **Step 7: 实现 OpenAICompatChat**

追加到 `llm.py`：

```python
class LlmClient(Protocol):
    def chat(self, system: str, user: str, *, max_tokens: int = 4000) -> str: ...


class OpenAICompatChat:
    """DeepSeek / SiliconFlow / 自定义：POST {base_url}/chat/completions。

    用 stdlib urllib 而不是引第三方 HTTP 库：一个 POST 就够，不值得多一个依赖。
    """

    def __init__(self, settings: LlmSettings, timeout: float = 120.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def chat(self, system: str, user: str, *, max_tokens: int = 4000) -> str:
        body = json.dumps(
            {
                "model": self.settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LlmError(_http_error_text(exc)) from exc
        except urllib.error.URLError as exc:
            raise LlmError(f"模型通道网络异常（网络或超时）：{exc.reason}") from exc

        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("模型通道返回了无法解析的响应") from exc


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    code = exc.code
    if code == 429:
        return f"模型通道被限流（429），请稍后重试：{exc.reason}"
    if code in (401, 403):
        return f"模型通道认证失败（{code}），请检查 API Key：{exc.reason}"
    return f"模型通道返回错误（{code}）：{exc.reason}"
```

- [ ] **Step 8: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_26_llm.py -q`
Expected: 4 passed

- [ ] **Step 9: 写失败测试（Claude 适配器）**

追加到 `tests/test_26_llm.py`：

```python
def test_anthropic_chat_uses_sdk_and_takes_final_text(monkeypatch):
    """Claude 通道走官方 SDK，不走 OpenAI 兼容层——这是规范要求。"""
    from fire_sight.services import llm

    captured = {}

    class _Block:
        type = "text"
        text = "## 火情态势\n..."

    class _Msg:
        content = [_Block()]

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_final_message(self):
            return _Msg()

    class _Messages:
        def stream(self, **kw):
            captured.update(kw)
            return _Stream()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(llm, "_anthropic_client_cls", lambda **kw: _Client())

    client = llm.AnthropicChat(llm.LlmSettings("claude", "https://api.anthropic.com", "sk-x", "claude-opus-5-5"))
    out = client.chat("system", "user")

    assert out.startswith("## 火情态势")
    assert captured["model"] == "claude-opus-5-5"
    assert captured["system"] == "system"
```

- [ ] **Step 10: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_26_llm.py -q`
Expected: FAIL（`AttributeError: ... no attribute '_anthropic_client_cls'` 或 `AnthropicChat` 未定义）

- [ ] **Step 11: 实现 AnthropicChat**

追加到 `llm.py`：

```python
def _anthropic_client_cls(**kw):
    """SDK 客户端构造点。抽出来是为了让单测能换掉它，不必真联网。"""
    import anthropic

    return anthropic.Anthropic(**kw)


class AnthropicChat:
    """Claude 通道：官方 anthropic SDK，模型默认 claude-opus-5-5。

    流式取最终文本——报告是长输出，非流式会撞 HTTP 超时。
    """

    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings
        self._client = _anthropic_client_cls(api_key=settings.api_key)

    def chat(self, system: str, user: str, *, max_tokens: int = 4000) -> str:
        try:
            with self._client.messages.stream(
                model=self.settings.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - SDK 异常类型多，统一转成可读原因
            raise LlmError(f"模型通道调用失败（网络或超时）：{exc}") from exc

        parts = [b.text for b in message.content if getattr(b, "type", "") == "text"]
        text = "".join(parts).strip()
        if not text:
            raise LlmError("模型通道返回了空内容")
        return text


def resolve_client() -> LlmClient:
    """按配置返回适配器。调用方不该关心走的是哪条协议。"""
    settings = resolve_settings()
    if settings.provider == "claude":
        return AnthropicChat(settings)
    return OpenAICompatChat(settings)
```

- [ ] **Step 12: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_26_llm.py -q`
Expected: 6 passed

- [ ] **Step 13: 加依赖、导出、全量回归**

1. `apps/fire-sight/pyproject.toml` 的 `[project].dependencies` 追加一行 `"anthropic>=0.40"`（若该节用列表格式则按现有条目风格）。
2. `apps/fire-sight/src/fire_sight/services/__init__.py` 追加导出：

```python
from fire_sight.services.llm import LlmError, LlmNotConfigured, resolve_client, resolve_settings
```

（若 `__init__.py` 有 `__all__`，把这四个名字补进去。）

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全绿（唯一失败项若仍是 `test_22_models_api.py::test_store_roundtrip`，那是会话前 `model_service.py` WIP 的既有失败，与本任务无关，记录即可）

- [ ] **Step 14: Commit**

```bash
git add apps/fire-sight/src/fire_sight/services/llm.py apps/fire-sight/src/fire_sight/services/__init__.py apps/fire-sight/pyproject.toml tests/test_26_llm.py
git commit -m "feat(llm): 可配置模型通道适配器（DeepSeek/SiliconFlow/Claude）"
```

---

### Task 2: 报告数据聚合、Prompt 与归档

**Files:**
- Create: `apps/fire-sight/src/fire_sight/services/report_service.py`
- Modify: `apps/fire-sight/src/fire_sight/services/__init__.py`
- Test: `tests/test_27_report_service.py`

**Interfaces:**
- Consumes: Task 1 的 `LlmNotConfigured`（仅在错误语义上对齐，本任务不调模型）；`fire_sight.alerting.EventStore.query_events`
- Produces:
  - `REPORTS_DIR: Path`（默认 `<workspace>/data/reports`，可被 `FIRESIGHT_REPORTS_DIR` 覆盖）
  - `aggregate_events(db_path: Path | str, start: str, end: str) -> dict`（形状见下方测试）
  - `build_prompt(summary: dict) -> tuple[str, str]`（system, user）
  - `make_report_id(now: datetime | None = None) -> str`
  - `save_report(report_id: str, meta: dict, markdown: str, *, directory: Path | None = None) -> Path`
  - `load_report(report_id: str, *, directory: Path | None = None) -> dict`（`{"meta": ..., "markdown": ...}`）
  - `list_reports(*, directory: Path | None = None) -> list[dict]`

- [ ] **Step 1: 写失败测试（聚合）**

创建 `tests/test_27_report_service.py`：

```python
"""报告服务：事件统计摘要、Prompt 组装、Markdown 归档。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _seed(db: Path, rows) -> None:
    """rows: (event_id, labels, conf, location, ts, is_fp)。ts 由测试指定。"""
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    with EventStore(db) as store:
        for eid, labels, conf, loc, _ts, fp in rows:
            store.insert(
                AlertEvent(
                    event_id=eid,
                    frame_index=1,
                    labels=list(labels),
                    conf=conf,
                    location=loc,
                    message=f"检测到{labels[0]}，位置：{loc}",
                )
            )
            if fp:
                store.mark_false_positive(eid)
    con = sqlite3.connect(str(db))
    for eid, _l, _c, _loc, ts, _fp in rows:
        con.execute("UPDATE alerts SET ts=? WHERE event_id=?", (ts, eid))
    con.commit()
    con.close()


def test_aggregate_empty_range(tmp_path):
    from fire_sight.services.report_service import aggregate_events

    db = tmp_path / "e.db"
    _seed(db, [("a", ["fire"], 0.9, "东门", "2026-01-05T10:00:00", False)])

    s = aggregate_events(db, "2026-03-01", "2026-03-31")
    assert s["total"] == 0
    assert s["by_class"] == {"fire": 0, "smoke": 0}
    assert s["samples"] == []
    assert len(s["by_hour"]) == 24


def test_aggregate_counts_classes_locations_and_samples(tmp_path):
    from fire_sight.services.report_service import aggregate_events

    db = tmp_path / "e.db"
    _seed(
        db,
        [
            ("a", ["fire"], 0.91, "东门", "2026-03-05T14:10:00", False),
            ("b", ["fire", "smoke"], 0.80, "东门", "2026-03-06T14:20:00", False),
            ("c", ["smoke"], 0.70, "仓库", "2026-03-07T09:00:00", True),
            ("d", ["fire"], 0.60, "仓库", "2026-03-08T09:30:00", False),
        ],
    )
    s = aggregate_events(db, "2026-03-01", "2026-03-31")

    assert s["total"] == 4
    assert s["by_class"]["fire"] == 3
    assert s["by_class"]["smoke"] == 2
    assert s["by_hour"][14] == 2
    assert s["by_hour"][9] == 2
    assert s["by_location"][0] == {"location": "东门", "count": 2}
    assert s["false_positives"] == 1
    assert s["false_positive_rate"] == pytest.approx(0.25, abs=1e-3)
    assert s["avg_conf"] == pytest.approx(0.7525, abs=1e-3)
    # 每类取置信度最高的 3 条
    fire_samples = [x for x in s["samples"] if x["label"] == "fire"]
    assert [x["event_id"] for x in fire_samples] == ["a", "b"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_27_report_service.py -q`
Expected: FAIL（`ModuleNotFoundError: ... report_service`）

- [ ] **Step 3: 实现聚合**

创建 `apps/fire-sight/src/fire_sight/services/report_service.py`：

```python
"""火情分析报告：统计摘要、Prompt 组装、Markdown 归档。

只喂**统计摘要**给模型，不喂原始事件：800 条事件既贵又会稀释重点，
而且模型会去复述个案而不是给结论。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fire_sight_platform.common.paths import DATA_DIR

# 归档目录。可被环境变量覆盖，便于测试与多实例
REPORTS_DIR: Path = Path(os.environ.get("FIRESIGHT_REPORTS_DIR") or (DATA_DIR / "reports"))

# 每类最多带几条典型事件进上下文
SAMPLES_PER_CLASS = 3
# 机位榜取前几
TOP_LOCATIONS = 5


def _rows_in_range(db_path: Path | str, start: str, end: str) -> List[Dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    from fire_sight.alerting import EventStore

    with EventStore(db_path) as store:
        return store.query_events(start=start, end=end, limit=100000, offset=0)


def aggregate_events(db_path: Path | str, start: str, end: str) -> Dict[str, Any]:
    """时间段内的统计摘要。形状稳定——Prompt 与前端都按它说话。"""
    rows = _rows_in_range(db_path, start, end)

    by_class = {"fire": 0, "smoke": 0}
    by_hour = [0] * 24
    by_loc: Dict[str, int] = {}
    confs: List[float] = []
    fp = 0

    for r in rows:
        labels = [x.strip() for x in str(r.get("labels") or "").split(",") if x.strip()]
        for lab in labels:
            if lab in by_class:
                by_class[lab] += 1
        loc = str(r.get("location") or "").strip() or "未标注位置"
        by_loc[loc] = by_loc.get(loc, 0) + 1
        try:
            by_hour[int(str(r.get("ts"))[11:13])] += 1
        except (ValueError, IndexError):
            pass
        confs.append(float(r.get("conf") or 0.0))
        if int(r.get("is_false_positive") or 0) == 1:
            fp += 1

    samples: List[Dict[str, Any]] = []
    for label in ("fire", "smoke"):
        picked = [r for r in rows if label in str(r.get("labels") or "")]
        picked.sort(key=lambda r: float(r.get("conf") or 0.0), reverse=True)
        for r in picked[:SAMPLES_PER_CLASS]:
            samples.append(
                {
                    "event_id": r.get("event_id"),
                    "label": label,
                    "conf": round(float(r.get("conf") or 0.0), 3),
                    "location": r.get("location") or "",
                    "message": r.get("message") or "",
                }
            )

    return {
        "range": {"start": start, "end": end},
        "total": len(rows),
        "by_class": by_class,
        "by_hour": by_hour,
        "by_location": [
            {"location": k, "count": v} for k, v in sorted(by_loc.items(), key=lambda kv: -kv[1])[:TOP_LOCATIONS]
        ],
        "false_positives": fp,
        "false_positive_rate": round(fp / len(rows), 4) if rows else 0.0,
        "avg_conf": round(sum(confs) / len(confs), 4) if confs else 0.0,
        "samples": samples,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_27_report_service.py -q`
Expected: 2 passed

- [ ] **Step 5: 写失败测试（Prompt）**

追加到 `tests/test_27_report_service.py`：

```python
def test_build_prompt_has_fixed_sections_and_data():
    import json

    from fire_sight.services.report_service import aggregate_events, build_prompt

    db = Path(__file__).resolve().parent  # 用假 db 路径不可行，直接构造摘要
    summary = {
        "range": {"start": "2026-03-01", "end": "2026-03-31"},
        "total": 4,
        "by_class": {"fire": 3, "smoke": 2},
        "by_hour": [0] * 24,
        "by_location": [{"location": "东门", "count": 2}],
        "false_positives": 1,
        "false_positive_rate": 0.25,
        "avg_conf": 0.75,
        "samples": [],
    }
    system, user = build_prompt(summary)

    for heading in (
        "## 火情态势",
        "## 时段与场所规律",
        "## 风险点",
        "## 防火建议",
        "## 处置优先级",
    ):
        assert heading in system
    assert "不得编造" in system or "不要编造" in system
    assert "2026-03-01" in user
    assert json.dumps(summary, ensure_ascii=False) in user
```

- [ ] **Step 6: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_27_report_service.py -q`
Expected: FAIL（`ImportError: cannot import name 'build_prompt'`）

- [ ] **Step 7: 实现 build_prompt**

追加到 `report_service.py`：

```python
_SYSTEM_PROMPT = """你是消防数据分析师。请基于用户给出的告警统计撰写一份中文报告。

硬性要求：
- 只依据给定数据，不得编造具体数字、时间、机位或案例；数据不足的项写「数据不足」。
- 输出 Markdown，必须包含且仅按顺序使用这五个二级标题：
## 火情态势
## 时段与场所规律
## 风险点
## 防火建议
## 处置优先级
- 「防火建议」按场所分条，每条可执行（做什么、在哪儿、多久一次）。
- 「处置优先级」分高/中/低三档，各附一句理由。
- 不要输出标题以外的开场白与结束语。
"""


def build_prompt(summary: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (system, user)。system 稳定可缓存，user 每次变。"""
    import json

    user = (
        f"时间范围：{summary['range']['start']} ~ {summary['range']['end']}\n"
        f"告警统计 JSON：\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        "请据此撰写报告。"
    )
    return _SYSTEM_PROMPT, user
```

- [ ] **Step 8: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_27_report_service.py -q`
Expected: 3 passed

- [ ] **Step 9: 写失败测试（归档）**

追加到 `tests/test_27_report_service.py`：

```python
def test_archive_save_load_list(tmp_path):
    from fire_sight.services.report_service import list_reports, load_report, save_report

    meta = {
        "title": "火情分析与防火建议 · 2026-03-01 ~ 2026-03-31",
        "start": "2026-03-01",
        "end": "2026-03-31",
        "model": "deepseek-chat",
        "created_at": "2026-09-29T10:00:00",
    }
    path = save_report("20260929-100000-abc123", meta, "# 报告正文", directory=tmp_path)

    assert path.suffix == ".md"
    assert path.with_suffix(".json").exists()
    assert path.read_text(encoding="utf-8") == "# 报告正文"

    loaded = load_report("20260929-100000-abc123", directory=tmp_path)
    assert loaded["markdown"] == "# 报告正文"
    assert loaded["meta"]["model"] == "deepseek-chat"

    items = list_reports(directory=tmp_path)
    assert len(items) == 1
    assert items[0]["id"] == "20260929-100000-abc123"
    assert items[0]["title"].startswith("火情分析")
```

- [ ] **Step 10: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_27_report_service.py -q`
Expected: FAIL（`ImportError: cannot import name 'save_report'`）

- [ ] **Step 11: 实现归档读写**

追加到 `report_service.py`：

```python
def make_report_id(now: Optional[datetime] = None) -> str:
    """<YYYYMMDD-HHMMSS>-<8位随机>。文件名即 id，排序即时间序。"""
    import uuid

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _dir(directory: Optional[Path]) -> Path:
    target = Path(directory) if directory is not None else REPORTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_report(
    report_id: str,
    meta: Dict[str, Any],
    markdown: str,
    *,
    directory: Optional[Path] = None,
) -> Path:
    """落盘 .md + .json 两份。JSON 是元数据（时间段、模型、统计快照），供回看列表用。"""
    import json

    base = _dir(directory) / report_id
    md_path = base.with_suffix(".md")
    md_path.write_text(markdown, encoding="utf-8")
    base.with_suffix(".json").write_text(
        json.dumps({**meta, "id": report_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_path


def load_report(report_id: str, *, directory: Optional[Path] = None) -> Dict[str, Any]:
    import json

    base = _dir(directory) / report_id
    md_path = base.with_suffix(".md")
    if not md_path.exists():
        raise FileNotFoundError(f"报告不存在: {report_id}")
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    return {"meta": meta, "markdown": md_path.read_text(encoding="utf-8")}


def list_reports(*, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    import json

    out: List[Dict[str, Any]] = []
    for p in sorted(_dir(directory).glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue  # 单个坏文件不该让列表打不开
    return out
```

- [ ] **Step 12: 跑测试确认通过 + 导出 + 全量回归**

1. Run: `.venv/Scripts/python.exe -m pytest tests/test_27_report_service.py -q` → 4 passed
2. `services/__init__.py` 追加 `from fire_sight.services.report_service import aggregate_events, build_prompt, list_reports, load_report, save_report`
3. Run: `.venv/Scripts/python.exe -m pytest -q` → 全绿（除既有 WIP 失败）

- [ ] **Step 13: Commit**

```bash
git add apps/fire-sight/src/fire_sight/services/report_service.py apps/fire-sight/src/fire_sight/services/__init__.py tests/test_27_report_service.py
git commit -m "feat(report): 事件统计摘要、Prompt 契约与 Markdown 归档"
```

---

### Task 3: 后台任务与 REST 接口

**Files:**
- Create: `apps/fire-sight/src/fire_sight/api/report_jobs.py`
- Create: `apps/fire-sight/src/fire_sight/api/routers/reports.py`
- Modify: `apps/fire-sight/src/fire_sight/api/schemas.py`
- Modify: `apps/fire-sight/src/fire_sight/api/app.py`（注册路由）
- Test: `tests/test_28_report_api.py`

**Interfaces:**
- Consumes: Task 1 `resolve_client()` / `LlmNotConfigured` / `LlmError`；Task 2 `aggregate_events` / `build_prompt` / `save_report` / `list_reports` / `load_report` / `make_report_id`
- Produces:
  - `class ReportJobManager`：`start(self, start: str, end: str) -> dict`、`get(self, job_id: str) -> dict | None`、`list(self) -> list[dict]`
  - `REPORTS: ReportJobManager`（进程内单例）
  - REST：`POST /api/reports`、`GET /api/reports/jobs/{id}`、`GET /api/reports`、`GET /api/reports/{id}`
  - Pydantic：`ReportRequest`、`ReportJobInfo`、`ReportListItem`、`ReportDetail`

- [ ] **Step 1: 写失败测试（生命周期）**

创建 `tests/test_28_report_api.py`：

```python
"""报告任务与 REST：生命周期、拒绝条件、归档回读。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    import fire_sight.services.report_service as rs

    monkeypatch.setattr(rs, "REPORTS_DIR", tmp_path / "reports")
    return tmp_path / "reports"


@pytest.fixture()
def fake_llm(monkeypatch):
    """假模型：返回带五节的正文，不联网、不花钱。"""
    import fire_sight.api.report_jobs as rj

    class _Fake:
        def chat(self, system, user, *, max_tokens=4000):
            return "\n".join(
                [
                    "## 火情态势",
                    "本月告警 4 起。",
                    "## 时段与场所规律",
                    "高发于 14 时。",
                    "## 风险点",
                    "仓库堆放可燃物。",
                    "## 防火建议",
                    "- 东门：每日巡查。",
                    "## 处置优先级",
                    "高：仓库。",
                ]
            )

    monkeypatch.setattr(rj, "resolve_client", lambda: _Fake())
    return _Fake


@pytest.fixture()
def seeded_db(tmp_path):
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    db = tmp_path / "events.db"
    with EventStore(db) as store:
        store.insert(
            AlertEvent(
                event_id="e1",
                frame_index=1,
                labels=["fire"],
                conf=0.9,
                location="东门",
                message="检测到明火，位置：东门",
            )
        )
    return db


def test_report_job_lifecycle(reports_dir, fake_llm, seeded_db, monkeypatch):
    from fire_sight.api import report_jobs
    from fire_sight.services import report_service

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    job = report_jobs.REPORTS.start("2026-09-01", "2026-09-30")
    assert job["status"] in {"queued", "running"}

    deadline = time.time() + 10
    while time.time() < deadline:
        j = report_jobs.REPORTS.get(job["id"])
        if j["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    j = report_jobs.REPORTS.get(job["id"])
    assert j["status"] == "done", j.get("error")
    assert j["report_id"]

    loaded = report_service.load_report(j["report_id"], directory=reports_dir)
    assert "## 火情态势" in loaded["markdown"]
    assert loaded["meta"]["start"] == "2026-09-01"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_28_report_api.py -q`
Expected: FAIL（`ModuleNotFoundError: ... report_jobs`）

- [ ] **Step 3: 实现任务管理器**

创建 `apps/fire-sight/src/fire_sight/api/report_jobs.py`：

```python
"""火情分析报告：后台任务 + 轮询。

为什么不是同步接口：生成要几十秒，同步 POST 会先撞前端超时，而且中途没有任何
反馈——用户看到的是一个卡住的按钮。为什么不用 SSE：进度粒度是「一次生成」，
两秒问一次足够，不值得占一条长连接（同 eval_jobs 的结论）。

不抢 GPU 锁：生成是网络 IO，既不占显存也不该跟训练/监控抢资源。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fire_sight_platform.common.paths import DATA_DIR

logger = logging.getLogger(__name__)

# 事件库位置。可被 monkeypatch，便于测试喂临时库
EVENTS_DB = DATA_DIR / "events" / "events.db"

# 状态机：queued → running → done | failed
TERMINAL = frozenset({"done", "failed"})


class ReportJobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(self._jobs[i]) for i in reversed(self._order) if i in self._jobs]

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, start: str, end: str) -> Dict[str, Any]:
        """起一次生成。无事件、未配通道由调用方先拦，这里只管跑。"""
        from fire_sight.services.report_service import aggregate_events, make_report_id

        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "running",
            "stage": "聚合数据",
            "start": start,
            "end": end,
            "error": None,
            "report_id": None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._jobs[job["id"]] = job
            self._order.append(job["id"])

        threading.Thread(
            target=self._run,
            args=(job, start, end, make_report_id()),
            daemon=True,
        ).start()
        return dict(job)

    def _set(self, job_id: str, **kw: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(kw)

    def _run(self, job: Dict[str, Any], start: str, end: str, report_id: str) -> None:
        """后台线程：聚合 → 生成 → 归档。任何一步失败都落到 failed 并带中文原因。"""
        from fire_sight.services.llm import LlmError, resolve_client
        from fire_sight.services.report_service import (
            aggregate_events,
            build_prompt,
            save_report,
        )

        job_id = job["id"]
        try:
            summary = aggregate_events(EVENTS_DB, start, end)
            self._set(job_id, stage="模型生成中")
            system, user = build_prompt(summary)
            client = resolve_client()
            markdown = client.chat(system, user)

            self._set(job_id, stage="归档中")
            meta = {
                "title": f"火情分析与防火建议 · {start} ~ {end}",
                "start": start,
                "end": end,
                "model": getattr(getattr(client, "settings", None), "model", ""),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "summary": summary,
            }
            save_report(report_id, meta, markdown)
            self._set(job_id, status="done", stage="", report_id=report_id)
        except Exception as exc:  # noqa: BLE001 - 单次生成失败不该让线程死掉
            logger.warning(f"报告生成失败: {exc}")
            self._set(job_id, status="failed", stage="", error=str(exc))


REPORTS = ReportJobManager()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_28_report_api.py -q`
Expected: 1 passed

- [ ] **Step 5: 写失败测试（拒绝条件与 REST）**

追加到 `tests/test_28_report_api.py`：

```python
def test_post_rejects_empty_range_and_missing_key(reports_dir, seeded_db, monkeypatch):
    import fire_sight.api.report_jobs as report_jobs
    import fire_sight.services.llm as llm
    from fire_sight.api import deps

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)

    # 区间无事件 → 400，说清「没有告警记录」
    resp = client.post("/api/reports", json={"start": "2020-01-01", "end": "2020-01-31"})
    assert resp.status_code == 400
    assert "没有告警" in resp.json()["detail"]

    # 未配置模型通道 → 400，附配置说明
    for name in ("FIRESIGHT_LLM_API_KEY", "DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)
    resp = client.post("/api/reports", json={"start": "2026-09-01", "end": "2026-09-30"})
    assert resp.status_code == 400
    assert "FIRESIGHT_LLM_API_KEY" in resp.json()["detail"]


def test_reports_rest_roundtrip(reports_dir, fake_llm, seeded_db, monkeypatch):
    import fire_sight.api.report_jobs as report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)
    created = client.post("/api/reports", json={"start": "2026-09-01", "end": "2026-09-30"}).json()

    deadline = time.time() + 10
    while time.time() < deadline:
        info = client.get(f"/api/reports/jobs/{created['id']}").json()
        if info["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    info = client.get(f"/api/reports/jobs/{created['id']}").json()
    assert info["status"] == "done", info.get("error")

    listing = client.get("/api/reports").json()
    assert len(listing) == 1
    rid = listing[0]["id"]

    detail = client.get(f"/api/reports/{rid}").json()
    assert "## 火情态势" in detail["markdown"]
    assert detail["meta"]["start"] == "2026-09-01"

    dl = client.get(f"/api/reports/{rid}", params={"download": 1})
    assert dl.status_code == 200
    assert "text/markdown" in dl.headers["content-type"]
```

- [ ] **Step 6: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_28_report_api.py -q`
Expected: 后两条 FAIL（404 未注册 / 缺 schema）

- [ ] **Step 7: 写 Schema 与路由**

`apps/fire-sight/src/fire_sight/api/schemas.py` 追加：

```python
# --------------------------------------------------------------------------
# 火情分析报告
# --------------------------------------------------------------------------


class ReportRequest(BaseModel):
    """一次生成请求。start/end 是日期（YYYY-MM-DD），含两端。"""

    start: str = Field(min_length=8, description="起始日期 YYYY-MM-DD")
    end: str = Field(min_length=8, description="结束日期 YYYY-MM-DD")


class ReportJobInfo(BaseModel):
    """一次生成任务。stage 是离散阶段标签——生成没有可信的百分比。"""

    id: str
    status: str = "running"
    stage: str = ""
    start: str = ""
    end: str = ""
    error: Optional[str] = None
    report_id: Optional[str] = None
    created_at: Optional[str] = None


class ReportListItem(BaseModel):
    id: str
    title: str = ""
    start: str = ""
    end: str = ""
    model: str = ""
    created_at: Optional[str] = None


class ReportDetail(BaseModel):
    meta: Dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""
```

创建 `apps/fire-sight/src/fire_sight/api/routers/reports.py`：

```python
"""火情分析报告接口：生成、轮询、历史、下载。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from fire_sight.api.report_jobs import EVENTS_DB, REPORTS
from fire_sight.api.schemas import ReportDetail, ReportJobInfo, ReportListItem, ReportRequest

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("", status_code=201, response_model=ReportJobInfo)
def create_report(req: ReportRequest) -> Dict[str, Any]:
    """起一次生成。无事件、未配通道都在这里拦掉——不让注定失败的任务白跑。"""
    from fire_sight.services.llm import LlmNotConfigured, resolve_settings
    from fire_sight.services.report_service import aggregate_events

    if req.end < req.start:
        raise HTTPException(status_code=400, detail="结束日期不能早于起始日期")

    summary = aggregate_events(EVENTS_DB, req.start, req.end)
    if summary["total"] == 0:
        raise HTTPException(
            status_code=400, detail="所选时间段没有告警记录，换一段有数据的区间再生成"
        )

    try:
        resolve_settings()
    except LlmNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return REPORTS.start(req.start, req.end)


@router.get("/jobs/{job_id}", response_model=ReportJobInfo)
def get_job(job_id: str) -> Dict[str, Any]:
    job = REPORTS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("", response_model=List[ReportListItem])
def list_reports() -> List[Dict[str, Any]]:
    from fire_sight.services.report_service import list_reports as _list

    return _list()


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(report_id: str, download: int = 0) -> Any:
    from fire_sight.services.report_service import load_report

    try:
        data = load_report(report_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if download:
        return PlainTextResponse(
            data["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
        )
    return data
```

`apps/fire-sight/src/fire_sight/api/app.py`：导入行加上 `reports`，并在 `app.include_router(stats.router)` 之后加 `app.include_router(reports.router)`。

- [ ] **Step 8: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_28_report_api.py -q`
Expected: 3 passed

- [ ] **Step 9: 全量回归**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全绿（除既有 WIP 失败）

- [ ] **Step 10: Commit**

```bash
git add apps/fire-sight/src/fire_sight/api/report_jobs.py apps/fire-sight/src/fire_sight/api/routers/reports.py apps/fire-sight/src/fire_sight/api/schemas.py apps/fire-sight/src/fire_sight/api/app.py tests/test_28_report_api.py
git commit -m "feat(report): 后台生成任务与报告 REST 接口"
```

---

### Task 4: 前端报告页

**Files:**
- Modify: `apps/web/package.json`（加 `marked` 依赖）
- Modify: `apps/web/src/api/types.ts`
- Modify: `apps/web/src/api/client.ts`
- Create: `apps/web/src/views/ReportView.vue`
- Modify: `apps/web/src/router/index.ts`
- Modify: `apps/web/src/App.vue`（顶栏入口）

**Interfaces:**
- Consumes: Task 3 的 REST（`POST /api/reports`、`GET /api/reports/jobs/{id}`、`GET /api/reports`、`GET /api/reports/{id}`）
- Produces: 路由 `/reports`；`api.createReport` / `api.reportJob` / `api.reports` / `api.report` / `api.reportDownloadUrl`

- [ ] **Step 1: 装依赖**

Run: `cd apps/web && npm install marked`
Expected: `package.json` 的 dependencies 出现 `"marked"`

- [ ] **Step 2: 加类型**

`apps/web/src/api/types.ts` 追加：

```typescript
// --------------------------------------------------------------------------
// 火情分析报告
// --------------------------------------------------------------------------

export type ReportJobStatus = 'running' | 'done' | 'failed'

export interface ReportJob {
  id: string
  status: ReportJobStatus
  /** 离散阶段标签：聚合数据 / 模型生成中 / 归档中。不做假百分比 */
  stage: string
  start: string
  end: string
  error: string | null
  report_id: string | null
  created_at: string | null
}

export interface ReportListItem {
  id: string
  title: string
  start: string
  end: string
  model: string
  created_at: string | null
}

export interface ReportDetail {
  meta: Record<string, unknown>
  markdown: string
}
```

- [ ] **Step 3: 加客户端方法**

`apps/web/src/api/client.ts` 的 `api` 对象里追加（`monitorStreamUrl` 之后），并在顶部 import 里加上 `ReportDetail, ReportJob, ReportListItem`：

```typescript
  // --- 分析报告 -------------------------------------------------------

  createReport: (start: string, end: string) =>
    request<ReportJob>('/reports', { method: 'POST', body: JSON.stringify({ start, end }) }),
  reportJob: (id: string) => request<ReportJob>(`/reports/jobs/${encodeURIComponent(id)}`),
  reports: () => request<ReportListItem[]>('/reports'),
  report: (id: string) => request<ReportDetail>(`/reports/${encodeURIComponent(id)}`),
  /** 下载交给浏览器原生下载（后端带 Content-Disposition）。 */
  reportDownloadUrl: (id: string) => `${BASE}/reports/${encodeURIComponent(id)}?download=1`,
```

- [ ] **Step 4: 写报告页**

创建 `apps/web/src/views/ReportView.vue`：

```vue
<script setup lang="ts">
/** 火情分析报告：选时间段 → 生成 → Markdown 展示 / 下载 / 历史回看。
 *
 * 生成是后台任务，这里只做两秒一次的轮询——进度粒度是「一次生成」，
 * 不必上 SSE（与 eval 任务同结论）。
 */
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'

import { api } from '@/api/client'
import type { ReportDetail, ReportJob, ReportListItem } from '@/api/types'

function thisMonthRange(): { start: string; end: string } {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const last = new Date(y, d.getMonth() + 1, 0).getDate()
  return { start: `${y}-${m}-01`, end: `${y}-${m}-${String(last).padStart(2, '0')}` }
}

const range = ref(thisMonthRange())
const job = ref<ReportJob | null>(null)
const detail = ref<ReportDetail | null>(null)
const history = ref<ReportListItem[]>([])
const error = ref('')
const busy = ref(false)

const running = computed(() => job.value?.status === 'running')
const html = computed(() =>
  detail.value ? (marked.parse(detail.value.markdown) as string) : '',
)

async function refreshHistory() {
  try {
    history.value = await api.reports()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function generate() {
  busy.value = true
  error.value = ''
  detail.value = null
  try {
    job.value = await api.createReport(range.value.start, range.value.end)
    const timer = window.setInterval(async () => {
      try {
        job.value = await api.reportJob(job.value!.id)
        if (job.value.status !== 'running') {
          window.clearInterval(timer)
          busy.value = false
          if (job.value.status === 'done' && job.value.report_id) {
            detail.value = await api.report(job.value.report_id)
            await refreshHistory()
          } else if (job.value.status === 'failed') {
            error.value = job.value.error || '生成失败'
          }
        }
      } catch (e) {
        window.clearInterval(timer)
        busy.value = false
        error.value = e instanceof Error ? e.message : String(e)
      }
    }, 2000)
  } catch (e) {
    // 400（无事件 / 未配通道）把后端原话显示出来
    error.value = e instanceof Error ? e.message : String(e)
    busy.value = false
  }
}

async function openReport(id: string) {
  error.value = ''
  try {
    detail.value = await api.report(id)
    job.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(refreshHistory)
</script>

<template>
  <div class="report">
    <div class="head">
      <h1 class="head__title">分析报告</h1>
      <p class="head__note">
        基于选定时间段的告警数据生成火情分析与防火建议，生成后归档可回看。
      </p>
    </div>

    <div v-if="error" class="fs-note-error" role="alert">{{ error }}</div>

    <section class="fs-viewfinder panel">
      <h2 class="fs-viewfinder__title">生成</h2>
      <div class="form">
        <div class="field field--narrow">
          <label class="field__label" for="rp-start">起始日期</label>
          <input id="rp-start" v-model="range.start" type="date" class="field__control" :disabled="running" />
        </div>
        <div class="field field--narrow">
          <label class="field__label" for="rp-end">结束日期</label>
          <input id="rp-end" v-model="range.end" type="date" class="field__control" :disabled="running" />
        </div>
        <div class="actions">
          <button type="button" class="fs-btn-primary" :disabled="busy || running" @click="generate">
            {{ running ? (job?.stage || '生成中…') : '生成报告' }}
          </button>
          <a v-if="detail" class="fs-btn-ghost btn-link" :href="api.reportDownloadUrl(String(detail.meta.id))">
            下载 .md
          </a>
        </div>
      </div>
    </section>

    <section v-if="detail" class="fs-viewfinder panel">
      <h2 class="fs-viewfinder__title">{{ String(detail.meta.title || '报告') }}</h2>
      <!-- eslint-disable-next-line vue/no-v-html —— 内容是本系统自己生成的 Markdown -->
      <article class="md" v-html="html"></article>
    </section>

    <section v-else class="fs-empty">
      选好时间段点「生成报告」。生成的报告会归档，可在下方列表回看。
    </section>

    <section class="fs-viewfinder panel">
      <h2 class="fs-viewfinder__title">历史报告</h2>
      <ul class="history">
        <li v-for="r in history" :key="r.id" class="history__item">
          <button type="button" class="history__btn" @click="openReport(r.id)">{{ r.title }}</button>
          <span class="history__meta">{{ r.created_at }} · {{ r.model }}</span>
        </li>
        <li v-if="!history.length" class="history__empty">还没有归档报告</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.report {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.head__title {
  margin: 0;
  font-size: var(--fs-xl);
  font-weight: 500;
  color: var(--bone);
}

.head__note {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}

.form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-5);
}

.actions {
  display: flex;
  gap: var(--sp-3);
  margin-left: auto;
}

.btn-link {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.md {
  font-size: var(--fs-sm);
  line-height: 1.8;
  color: var(--bone);
}

.md :deep(h2) {
  margin: var(--sp-5) 0 var(--sp-2);
  font-size: var(--fs-md);
  font-weight: 500;
  color: var(--bone);
}

.history {
  margin: 0;
  padding: 0;
  list-style: none;
}

.history__item {
  display: flex;
  align-items: baseline;
  gap: var(--sp-4);
  padding: var(--sp-2) 0;
  border-bottom: var(--hairline);
}

.history__btn {
  padding: 0;
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--bone);
  background: transparent;
  border: 0;
  cursor: pointer;
  text-align: left;
}

.history__btn:hover {
  color: var(--accent);
}

.history__meta {
  margin-left: auto;
  font-size: var(--fs-xs);
  color: var(--dross);
}

.history__empty {
  padding: var(--sp-3) 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}
</style>
```

- [ ] **Step 5: 挂路由与顶栏入口**

`apps/web/src/router/index.ts` 在 `monitor` 路由之后追加：

```typescript
    {
      path: '/reports',
      name: 'reports',
      component: () => import('@/views/ReportView.vue'),
      meta: { title: '分析报告' },
    },
```

`apps/web/src/App.vue` 的 `NAV` 数组里，`训练中心` 之后插入一项：

```typescript
  { to: '/reports', label: '分析报告' },
```

- [ ] **Step 6: 类型检查**

Run: `cd apps/web && npm run typecheck`
Expected: 无输出（通过）

- [ ] **Step 7: Commit**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/src/api/types.ts apps/web/src/api/client.ts apps/web/src/views/ReportView.vue apps/web/src/router/index.ts apps/web/src/App.vue
git commit -m "feat(web): 分析报告页——生成、Markdown 展示、下载与历史回看"
```

---

### Task 5: E2E 与文档

**Files:**
- Create: `apps/web/e2e/report.spec.ts`
- Modify: `docs/api/REST-API.md`
- Modify: `docs/api/测试说明.md`
- Modify: `docs/ROADMAP.md`（把「LLM 智能报告」从可选扩展划掉）

**Interfaces:**
- Consumes: Task 3/4 的 REST 与页面
- Produces: 可回归的 e2e 用例；接口文档条目

- [ ] **Step 1: 写 e2e**

创建 `apps/web/e2e/report.spec.ts`：

```typescript
import { expect, test } from '@playwright/test'

/** 分析报告页：结构、拒绝条件、生成冒烟（真调模型，仅在配了 key 时跑）。 */

test('报告页结构：时间选择、生成按钮、历史区都在', async ({ page }) => {
  await page.goto('/reports')

  await expect(page.getByRole('heading', { name: '分析报告' })).toBeVisible()
  await expect(page.getByLabel('起始日期')).toBeVisible()
  await expect(page.getByLabel('结束日期')).toBeVisible()
  await expect(page.getByRole('button', { name: /生成报告|生成中/ })).toBeVisible()
  await expect(page.getByText('历史报告')).toBeVisible()
})

test('区间无事件时拒绝生成并说清原因', async ({ page }) => {
  await page.goto('/reports')

  await page.getByLabel('起始日期').fill('2020-01-01')
  await page.getByLabel('结束日期').fill('2020-01-31')
  await page.getByRole('button', { name: /生成报告/ }).click()

  await expect(page.getByRole('alert')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('alert')).toContainText(/没有告警|未配置/)
})

test('生成冒烟：真调模型并出现五节标题', async ({ page }) => {
  test.skip(!process.env.FIRESIGHT_LLM_API_KEY, '未配 FIRESIGHT_LLM_API_KEY，跳过真调模型的用例')

  await page.goto('/reports')
  await page.getByRole('button', { name: /生成报告/ }).click()

  // 生成要几十秒：等「火情态势」这一节出现
  await expect(page.getByRole('heading', { name: '火情态势' })).toBeVisible({ timeout: 120_000 })
  await expect(page.getByRole('button', { name: '下载 .md' })).toBeVisible()
})
```

- [ ] **Step 2: 起服务并跑 e2e**

```bash
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/firesight.ps1 start
cd apps/web && npm run build && npx playwright test e2e/report.spec.ts
```

Expected: 2 passed, 1 skipped（未配 `FIRESIGHT_LLM_API_KEY` 时）；配了 key 则 3 passed

- [ ] **Step 3: 补接口文档**

`docs/api/REST-API.md` 追加一节（放在现有事件/模型接口之后）：

```markdown
## 分析报告

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/reports` | `{start, end}` 生成报告任务 → 201 `{id, status}`。区间无事件或未配置模型通道 → 400 |
| GET | `/api/reports/jobs/{id}` | 任务状态：`{id, status, stage, error, report_id}` |
| GET | `/api/reports` | 历史归档列表 |
| GET | `/api/reports/{id}` | 报告全文 `{meta, markdown}`；`?download=1` 下载 .md |

模型通道由环境变量配置：`FIRESIGHT_LLM_PROVIDER`（deepseek / siliconflow / claude / custom）、
`FIRESIGHT_LLM_API_KEY`、`FIRESIGHT_LLM_BASE_URL`、`FIRESIGHT_LLM_MODEL`。
归档落在 `data/reports/`。
```

`docs/api/测试说明.md` 的用例表追加两行（`26` 与 `27/28`），内容分别对应 `test_26_llm.py`（模型通道适配器）与 `test_27_report_service.py` / `test_28_report_api.py`（报告服务与接口）。

`docs/ROADMAP.md` 的「阶段外可选扩展」表里，把「LLM 智能报告」一行改为已完成（或移入 S5 已完成清单）。

- [ ] **Step 4: 全量回归**

```bash
cd /e/FireSight && .venv/Scripts/python.exe -m pytest -q
cd apps/web && npx playwright test
```

Expected: pytest 全绿（除既有 `model_service.py` WIP 失败）；e2e 全绿（2 skipped 为既有跳过）

- [ ] **Step 5: Commit**

```bash
git add apps/web/e2e/report.spec.ts docs/api/REST-API.md docs/api/测试说明.md docs/ROADMAP.md
git commit -m "test(report): 报告页 e2e 与接口文档"
```

---

## Self-Review 记录

1. **Spec 覆盖**：§4 LLM 通道 → Task 1；§5 数据聚合 / §6 Prompt / §7 归档 → Task 2；§7 REST 与任务 → Task 3；§8 前端 → Task 4；§9 测试与文档 → Task 5。无缺口。
2. **占位符扫描**：所有代码块为可落盘的完整内容；无 TBD /「类似 Task N」。
3. **类型一致性**：`resolve_client() -> LlmClient`、`aggregate_events(db_path, start, end) -> dict`、`build_prompt(summary) -> tuple[str, str]`、`save_report(report_id, meta, markdown, *, directory=None) -> Path`、`REPORTS.start(start, end) -> dict` —— 各任务引用一致；`stage` 字段在 schema、任务管理器、前端三处同名。
