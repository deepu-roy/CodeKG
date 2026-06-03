"""Embedding provider protocol and base types."""

from typing import Protocol, runtime_checkable

from code_kg.config import EmbeddingSettings


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    All providers must be async and return list[float] vectors.
    The canonical batch method is ``embed_batch``; ``embed`` is a convenience
    wrapper for single-item callers.
    """

    name: str
    """Provider identifier (e.g. "sentence_transformers", "openai")."""

    @property
    def dimensions(self) -> int:
        """Return the dimensionality of produced embeddings."""
        ...

    async def embed(self, text: str) -> list[float]:
        """Embed a single string (convenience wrapper around embed_batch).

        Args:
            text: The text to embed.

        Returns:
            A list of floats (the embedding vector).
        """
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple strings in one call (the primary batch entry point).

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, same order as input.
        """
        ...


def build_embedding_provider(settings: EmbeddingSettings) -> "EmbeddingProvider":
    """Factory: construct the correct EmbeddingProvider from settings.

    Args:
        settings: Embedding configuration.

    Returns:
        Configured EmbeddingProvider instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if settings.provider == "sentence_transformers":
        from code_kg.providers.embedding.sentence_transformers import SentenceTransformerProvider
        return SentenceTransformerProvider(settings)
    elif settings.provider == "openai":
        from code_kg.providers.embedding.openai import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider(settings)
    else:
        raise ValueError(f"Unknown embedding provider: {settings.provider!r}")
