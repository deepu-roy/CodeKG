"""OpenAI embedding provider (remote, cloud)."""

import logging

import httpx

from code_kg.config import EmbeddingSettings

logger = logging.getLogger(__name__)

_API_URL = "https://api.openai.com/v1/embeddings"


class OpenAIEmbeddingProvider:
    """Embedding provider backed by the OpenAI Embeddings API.

    Compatible with any OpenAI-spec endpoint (OpenAI, Azure, etc.).
    Uses httpx for async HTTP requests.
    """

    name: str = "openai"

    def __init__(self, settings: EmbeddingSettings) -> None:
        if not settings.api_key:
            raise ValueError(
                "OpenAI embedding provider requires EMBEDDING__API_KEY to be set"
            )
        self._settings = settings
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    @property
    def dimensions(self) -> int:
        return self._settings.dimensions

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector as list[float].
        """
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via OpenAI API.

        Sends texts in chunks of batch_size to stay within API limits.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        batch_size = self._settings.batch_size

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            payload = {
                "model": self._settings.model,
                "input": chunk,
                "encoding_format": "float",
            }
            response = await self._client.post(_API_URL, json=payload)
            response.raise_for_status()
            data = response.json()

            # OpenAI returns data sorted by index
            chunk_vectors = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
            all_vectors.extend(chunk_vectors)
            logger.debug(f"OpenAI embedded batch {i // batch_size + 1} ({len(chunk)} texts)")

        return all_vectors

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
