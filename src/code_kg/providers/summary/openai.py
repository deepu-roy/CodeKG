"""OpenAI-compatible summary provider (cloud LLM)."""

import logging

from code_kg.config import SummarySettings
from code_kg.domain.models import SummaryRequest, SummaryResponse
from code_kg.providers.llm.client import LLMClient
from code_kg.providers.summary.prompts import build_prompt
from code_kg.providers.summary.response import parse_response, fallback_response

logger = logging.getLogger(__name__)

_CHAT_PATH = "/v1/chat/completions"


class OpenAIProvider:
    """Summary provider backed by any OpenAI-compatible /v1/chat/completions endpoint.

    Works with OpenAI, Azure OpenAI, LM Studio, etc.
    Requests JSON-mode output via ``response_format``.
    """

    name: str = "openai"

    def __init__(self, settings: SummarySettings) -> None:
        if not settings.api_key:
            raise ValueError(
                "OpenAI summary provider requires SUMMARY__API_KEY to be set"
            )
        self._settings = settings
        self._model_version = settings.model
        self._client = LLMClient(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=120.0,
        )

    @property
    def model_version(self) -> str:
        """Model identifier — used for cache invalidation."""
        return self._model_version

    async def summarise(self, request: SummaryRequest) -> SummaryResponse:
        """Generate a summary using the configured OpenAI-compatible model.

        Args:
            request: Summary request.

        Returns:
            SummaryResponse with summary, tags, and complexity.
        """
        system_prompt, user_prompt = build_prompt(request)
        try:
            content = await self._client.chat(
                path=_CHAT_PATH,
                model=self._settings.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._settings.temperature,
                response_format={"type": "json_object"},
            )
            return parse_response(content)
        except Exception as e:
            logger.error(f"OpenAI error for {request.name}: {e}")
            return fallback_response(request)

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
