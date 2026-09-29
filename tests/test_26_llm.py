"""LLM 通道：配置解析、OpenAI 兼容适配器、Claude 适配器。"""

from __future__ import annotations

import json
import sys
import urllib.error
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


def test_anthropic_sdk_available_and_has_streaming_surface():
    """验证 anthropic 包已安装且提供 AnthropicChat 用到的 API 表面。

    不联网：只检查构造与方法存在，确保下游 provider=claude 不会在 __init__ 里
    因缺包而崩，也不因 SDK 接口变动而悄悄失效。
    """
    import anthropic

    assert hasattr(anthropic, "Anthropic"), "anthropic.Anthropic 不存在"
    client = anthropic.Anthropic(api_key="sk-test")
    assert hasattr(client, "messages"), "client.messages 不存在"
    assert hasattr(client.messages, "stream"), "client.messages.stream 不存在"
    # stream() 返回 MessageStreamManager，上下文里拿到 MessageStream
    from anthropic.lib.streaming._messages import MessageStream

    assert hasattr(MessageStream, "get_final_message"), "MessageStream.get_final_message 不存在"
