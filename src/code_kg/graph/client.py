"""Neo4j client wrapper with connection pooling and retry logic."""

import asyncio
import logging
from typing import Any, Optional

from neo4j import AsyncGraphDatabase, AsyncSession, basic_auth

from code_kg.config import Neo4jSettings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Async Neo4j client with pooling and retry logic."""

    def __init__(self, settings: Neo4jSettings, max_retries: int = 3):
        """
        Initialize Neo4j client.

        Args:
            settings: Neo4j connection settings.
            max_retries: Max retries for transient failures.
        """
        self.settings = settings
        self.max_retries = max_retries
        self._driver: Optional[Any] = None

    async def connect(self):
        """Connect to Neo4j and return the driver."""
        if self._driver is None:
            encrypted = self.settings.uri.startswith("neo4j+s") or self.settings.uri.startswith("bolt+s")
            self._driver = AsyncGraphDatabase.driver(
                uri=self.settings.uri,
                auth=basic_auth(self.settings.user, self.settings.password),
                encrypted=encrypted,
            )
            logger.info(f"Connected to Neo4j at {self.settings.uri}")
        return self._driver

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            logger.info("Closed Neo4j connection")

    async def get_session(self) -> AsyncSession:
        """Get an async session for the configured database."""
        driver = await self.connect()
        return driver.session(database=self.settings.database)

    async def execute_query(
        self,
        query: str,
        parameters: Optional[dict[str, Any]] = None,
        retries: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Execute a Cypher query with retry logic.

        Args:
            query: Cypher query string.
            parameters: Query parameters.
            retries: Current retry count (used internally).

        Returns:
            List of result records as dicts.
        """
        session = await self.get_session()
        try:
            result = await session.run(query, parameters or {})
            records = await result.data()
            return records if records else []
        except Exception as e:
            if retries < self.max_retries:
                logger.warning(
                    f"Query failed (retry {retries + 1}/{self.max_retries}): {e}"
                )
                await asyncio.sleep(0.5 * (2 ** retries))  # exponential backoff
                return await self.execute_query(query, parameters, retries + 1)
            logger.error(f"Query failed after {self.max_retries} retries: {e}")
            raise
        finally:
            await session.close()

    async def execute_write(
        self,
        query: str,
        parameters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a write query in a transaction.

        Args:
            query: Cypher query string.
            parameters: Query parameters.

        Returns:
            Summary of the write operation.
        """
        session = await self.get_session()
        try:
            async with session.begin_transaction() as tx:
                result = await tx.run(query, parameters or {})
                summary = await result.consume()
            return {
                "nodes_created": summary.counters.nodes_created,
                "nodes_deleted": summary.counters.nodes_deleted,
                "properties_set": summary.counters.properties_set,
                "relationships_created": summary.counters.relationships_created,
                "relationships_deleted": summary.counters.relationships_deleted,
            }
        finally:
            await session.close()

    async def health_check(self) -> bool:
        """Check if the Neo4j connection is healthy."""
        try:
            await self.execute_query("RETURN 1 AS healthcheck")
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
