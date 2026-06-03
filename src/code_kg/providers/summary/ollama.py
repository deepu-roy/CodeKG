"""Ollama summary provider (local LLM)."""

import logging

from code_kg.config import SummarySettings
from code_kg.domain.models import SummaryRequest, SummaryResponse
from code_kg.providers.llm.client import LLMClient
from code_kg.providers.summary.prompts import build_prompt
from code_kg.providers.summary.response import parse_response, fallback_response

logger = logging.getLogger(__name__)


class OllamaProvider:
    """Summary provider backed by a local Ollama instance.

    Uses the Ollama ``/api/chat`` endpoint. Retry logic lives in LLMClient.
    """

    name: str = "ollama"

    def __init__(self, settings: SummarySettings) -> None:
        self._settings = settings
        self._model_version = settings.model
        self._client = LLMClient(
            base_url=settings.base_url,
            timeout=120.0,
        )

    @property
    def model_version(self) -> str:
        """Model identifier — used for cache invalidation."""
        return self._model_version

    async def summarise(self, request: SummaryRequest) -> SummaryResponse:
        """Generate a summary using the configured Ollama model.

        Args:
            request: Summary request.

        Returns:
            SummaryResponse with summary, tags, and complexity.
        """
        system_prompt, user_prompt = build_prompt(request)
        try:
            content = await self._client.chat(
                path="/api/chat",
                model=self._settings.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._settings.temperature,
            )
            return parse_response(content)
        except Exception as e:
            logger.error(f"Ollama error for {request.name}: {e}")
            return fallback_response(request)

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
