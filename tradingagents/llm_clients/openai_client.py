import os
from typing import Any, Optional

from langchain_openai import ChatOpenAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model


class NormalizedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with normalized content output.

    Some OpenAI-compatible backends return content as a list of typed blocks
    (reasoning, text, etc.). This normalizes to string for consistent
    downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))

# Kwargs forwarded from user config to ChatOpenAI
_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "max_tokens", "temperature",
    "api_key", "callbacks", "http_client", "http_async_client",
    "default_headers",
)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OLLAMA_BASE_URL = "http://localhost:11434/v1"

_OPENROUTER_PROVIDER_PREFIX = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "xai": "x-ai",
}

# Preserve the existing user-facing model names while converting to the
# canonical OpenRouter model ids that require a provider prefix.
_OPENROUTER_MODEL_ALIASES = {
    "anthropic": {
        "claude-opus-4-6": "claude-opus-4.6",
        "claude-sonnet-4-6": "claude-sonnet-4.6",
        "claude-opus-4-5": "claude-opus-4.5",
        "claude-sonnet-4-5": "claude-sonnet-4.5",
        "claude-haiku-4-5": "claude-haiku-4.5",
    },
    "xai": {
        "grok-4-0709": "grok-4-07-09",
    },
}


def _normalize_openrouter_model(provider: str, model: str) -> str:
    """Convert provider-local model names into canonical OpenRouter ids."""
    if "/" in model:
        return model

    if provider not in _OPENROUTER_PROVIDER_PREFIX:
        return model

    canonical_model = _OPENROUTER_MODEL_ALIASES.get(provider, {}).get(model, model)
    return f"{_OPENROUTER_PROVIDER_PREFIX[provider]}/{canonical_model}"


def _map_reasoning_effort(raw_effort: Optional[str]) -> Optional[str]:
    if not raw_effort:
        return None

    effort = raw_effort.lower()
    if effort == "none":
        return "none"
    return effort


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI-compatible backends.

    All hosted providers in this project are routed through OpenRouter's
    OpenAI-compatible API. For `provider="openrouter"`, use the dedicated
    OpenRouter client so arbitrary model ids pass through untouched. Ollama
    remains local and uses its local endpoint.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance."""
        llm_kwargs = {"model": self.model}

        if self.provider == "ollama":
            llm_kwargs["base_url"] = self.base_url or _OLLAMA_BASE_URL
            llm_kwargs["api_key"] = self.kwargs.get("api_key", "ollama")
        else:
            llm_kwargs["model"] = _normalize_openrouter_model(self.provider, self.model)
            llm_kwargs["base_url"] = self.base_url or _OPENROUTER_BASE_URL
            api_key = self.kwargs.get("api_key") or os.environ.get("OPENROUTER_API_KEY")
            if api_key:
                llm_kwargs["api_key"] = api_key

        # Forward user-provided kwargs
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        reasoning_effort = _map_reasoning_effort(self.kwargs.get("reasoning_effort"))
        if self.provider == "anthropic":
            reasoning_effort = _map_reasoning_effort(self.kwargs.get("effort")) or reasoning_effort
        elif self.provider == "google":
            reasoning_effort = _map_reasoning_effort(self.kwargs.get("thinking_level")) or reasoning_effort

        extra_body = dict(self.kwargs.get("extra_body", {}))
        if reasoning_effort and self.provider != "ollama":
            reasoning = dict(extra_body.get("reasoning", {}))
            reasoning["effort"] = reasoning_effort
            extra_body["reasoning"] = reasoning

        if extra_body:
            llm_kwargs["extra_body"] = extra_body

        return NormalizedChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for the provider."""
        return validate_model(self.provider, self.model)
