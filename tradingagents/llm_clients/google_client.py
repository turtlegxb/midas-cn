from typing import Optional

from .openai_client import OpenAIClient
from .validators import validate_model


class GoogleClient(OpenAIClient):
    """Gemini models routed through OpenRouter's OpenAI-compatible API."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, provider="google", **kwargs)

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)
