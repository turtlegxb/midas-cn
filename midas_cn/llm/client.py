from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Protocol
from urllib import error, request


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat client for provider-neutral report synthesis."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return LLMResponse(content=content, provider=self.provider, model=self.model)


class AnthropicClient:
    """Minimal Anthropic Messages API client."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
        max_tokens: int = 4096,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> LLMResponse:
        system_parts = [item["content"] for item in messages if item.get("role") == "system"]
        user_messages = [item for item in messages if item.get("role") != "system"]
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "messages": user_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        data = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/v1/messages",
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content_blocks = body.get("content") or []
        content = "".join(
            str(block.get("text") or "")
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return LLMResponse(content=content, provider=self.provider, model=self.model)


def build_llm_client(config: dict) -> LLMClient | None:
    if not bool(config.get("enabled", False)):
        return None
    provider = str(config.get("provider", "openai")).strip().lower()
    model = str(config.get("model_name") or config.get("quick_model") or config.get("model") or "gpt-4o-mini").strip()
    base_url = str(config.get("base_url") or "https://api.openai.com/v1").strip()
    api_key_env = str(config.get("api_key_env") or "OPENAI_API_KEY").strip()
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        return None
    if provider in {"claude", "anthropic"}:
        return AnthropicClient(
            provider=provider,
            model=model,
            base_url=base_url or "https://api.anthropic.com",
            api_key=api_key,
            timeout=float(config.get("timeout_seconds", 20)),
            max_tokens=int(config.get("max_tokens", 4096)),
        )
    if provider in {"openai", "deepseek", "qwen", "glm", "openrouter", "aihubmix", "ollama", "custom_openai"}:
        return OpenAICompatibleClient(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=float(config.get("timeout_seconds", 20)),
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")


def compact_llm_error(exc: Exception) -> str:
    if isinstance(exc, error.HTTPError):
        return f"模型服务返回HTTP {exc.code}"
    if isinstance(exc, error.URLError):
        return f"模型服务连接失败：{exc.reason}"
    return str(exc)
