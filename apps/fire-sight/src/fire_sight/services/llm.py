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
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise LlmError(_http_error_text(exc)) from exc
        except urllib.error.URLError as exc:
            raise LlmError(f"模型通道网络异常（网络或超时）：{exc.reason}") from exc

        # 200 不等于 JSON：网关错误页、代理截断都会走这里。JSONDecodeError /
        # UnicodeDecodeError 是英文裸异常，直接漏出去违反「可读中文错误」契约。
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LlmError(f"模型通道返回了无法解析的响应（不是合法 JSON）：{exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("模型通道返回了无法解析的响应") from exc
        # content 为 null / 空串时 str() 会变成 "None" 并被当成报告正文归档——
        # 与 Claude 适配器对齐：空内容就是失败
        if content is None or not str(content).strip():
            raise LlmError("模型通道返回了空内容")
        return str(content)


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    code = exc.code
    if code == 429:
        return f"模型通道被限流（429），请稍后重试：{exc.reason}"
    if code in (401, 403):
        return f"模型通道认证失败（{code}），请检查 API Key：{exc.reason}"
    return f"模型通道返回错误（{code}）：{exc.reason}"


def _anthropic_client_cls(**kw):
    """SDK 客户端构造点。抽出来是为了让单测能换掉它，不必真联网。"""
    import anthropic

    return anthropic.Anthropic(**kw)


#: Claude 路径的默认输出预算。claude-opus-5-5 的自适应思考计入输出 token，
#: 4000 会把一份长报告拦腰截断；FIRESIGHT_LLM_MAX_TOKENS 可按需调高/调低。
CLAUDE_MAX_TOKENS_DEFAULT = 16000


def _claude_max_tokens() -> int:
    raw = (os.environ.get("FIRESIGHT_LLM_MAX_TOKENS") or "").strip()
    try:
        return int(raw) if raw else CLAUDE_MAX_TOKENS_DEFAULT
    except ValueError:
        return CLAUDE_MAX_TOKENS_DEFAULT  # 写坏了回落到默认，不把 ValueError 甩给用户


class AnthropicChat:
    """Claude 通道：官方 anthropic SDK，模型默认 claude-opus-5-5。

    流式取最终文本——报告是长输出，非流式会撞 HTTP 超时。
    """

    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings
        # base_url 也传：否则 FIRESIGHT_LLM_BASE_URL 对 claude 通道是死配置
        self._client = _anthropic_client_cls(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
        )

    def chat(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        try:
            with self._client.messages.stream(
                model=self.settings.model,
                max_tokens=max_tokens if max_tokens is not None else _claude_max_tokens(),
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
