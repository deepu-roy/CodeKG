"""Sentence-transformers embedding provider (local, default)."""

import asyncio
import logging
from functools import cached_property
from typing import Any

from code_kg.config import EmbeddingSettings

logger = logging.getLogger(__name__)


class SentenceTransformerProvider:
    """Embedding provider backed by sentence-transformers (runs locally).

    The model is loaded lazily on first use and cached. All encode calls
    are run in a thread-pool executor to avoid blocking the event loop.
    """

    name: str = "sentence_transformers"

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._model: Any | None = None

    @property
    def dimensions(self) -> int:
        return self._settings.dimensions

    def _get_model(self) -> Any:
        """Lazy-load and cache the SentenceTransformer model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError(
                    "sentence-transformers is required. "
                    "Install with: pip install sentence-transformers"
                ) from e
            logger.info(f"Loading sentence-transformer model: {self._settings.model}")
            try:
                # Use cached files if available — avoids HuggingFace redirect
                # round-trips on every startup when the model is already present.
                self._model = SentenceTransformer(
                    self._settings.model, local_files_only=True
                )
            except Exception:
                logger.info("Model not in local cache — downloading from HuggingFace")
                self._model = SentenceTransformer(self._settings.model)
        return self._model

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
        """Embed a batch of texts.

        Runs encoding in a thread-pool to avoid blocking the event loop.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        loop = asyncio.get_event_loop()
        model = self._get_model()

        def _encode() -> list[list[float]]:
            embeddings = model.encode(
                texts,
                batch_size=self._settings.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return embeddings.tolist()

        vectors = await loop.run_in_executor(None, _encode)
        logger.debug(f"Embedded {len(texts)} texts with {self._settings.model}")
        return vectors
