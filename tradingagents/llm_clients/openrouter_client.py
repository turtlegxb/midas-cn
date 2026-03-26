from typing import Optional

from .openai_client import OpenAIClient
from .validators import validate_model


class OpenRouterClient(OpenAIClient):
    """Generic OpenRouter client for arbitrary OpenRouter model ids."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, provider="openrouter", **kwargs)

    def validate_model(self) -> bool:
        """Validate model for OpenRouter."""
        return validate_model("openrouter", self.model)
