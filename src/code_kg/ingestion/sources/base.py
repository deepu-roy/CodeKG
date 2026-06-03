"""Base class and protocol for code ingestion sources."""

from typing import AsyncIterator, Protocol

from code_kg.domain.models import RawEdge, RawNode


class IngestionSource(Protocol):
    """Protocol for code ingestion sources."""

    name: str
    supported_extensions: list[str]

    async def extract(
        self,
        repo_root: str,
        files: list[str],
    ) -> AsyncIterator[RawNode | RawEdge]:
        """
        Extract nodes and edges from the given files.

        Args:
            repo_root: Root directory of the repository.
            files: List of file paths relative to repo_root.

        Yields:
            RawNode or RawEdge objects.
        """
        ...
