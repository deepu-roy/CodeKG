"""Summary provider protocol and base types."""

from typing import Protocol, runtime_checkable

from code_kg.config import SummarySettings
from code_kg.domain.models import SummaryRequest, SummaryResponse


@runtime_checkable
class SummaryProvider(Protocol):
    """Protocol for summary / LLM providers."""

    name: str
    """Provider identifier (e.g. "ollama", "openai")."""

    @property
    def model_version(self) -> str:
        """Model identifier — used for cache invalidation when model changes."""
        ...

    async def summarise(self, request: SummaryRequest) -> SummaryResponse:
        """Generate a natural-language summary for a code node.

        Args:
            request: Summary request with node metadata and code/text.

        Returns:
            SummaryResponse with summary, tags, and complexity.
        """
        ...


def build_summary_provider(settings: SummarySettings) -> "SummaryProvider":
    """Factory: construct the correct SummaryProvider from settings.

    Args:
        settings: Summary/LLM configuration.

    Returns:
        Configured SummaryProvider instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if settings.provider == "ollama":
        from code_kg.providers.summary.ollama import OllamaProvider
        return OllamaProvider(settings)
    elif settings.provider == "openai":
        from code_kg.providers.summary.openai import OpenAIProvider
        return OpenAIProvider(settings)
    else:
        raise ValueError(f"Unknown summary provider: {settings.provider!r}")
