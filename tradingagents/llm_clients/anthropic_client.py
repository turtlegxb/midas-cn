from typing import Optional

from .openai_client import OpenAIClient
from .validators import validate_model


class AnthropicClient(OpenAIClient):
    """Claude models routed through OpenRouter's OpenAI-compatible API."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, provider="anthropic", **kwargs)

    def validate_model(self) -> bool:
        """Validate model for Anthropic."""
        return validate_model("anthropic", self.model)
