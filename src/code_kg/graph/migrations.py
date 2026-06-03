"""Neo4j schema migrations — constraints, indexes, and vector indexes."""

import asyncio
import logging

from code_kg.config import Settings
from code_kg.graph.client import Neo4jClient

logger = logging.getLogger(__name__)

# Uniqueness constraints
CONSTRAINTS = [
    "CREATE CONSTRAINT file_id IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT class_id IF NOT EXISTS FOR (n:Class) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT interface_id IF NOT EXISTS FOR (n:Interface) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT function_id IF NOT EXISTS FOR (n:Function) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT method_id IF NOT EXISTS FOR (n:Method) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT endpoint_id IF NOT EXISTS FOR (n:Endpoint) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT section_id IF NOT EXISTS FOR (n:Section) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT layer_id IF NOT EXISTS FOR (n:Layer) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT concept_id IF NOT EXISTS FOR (n:Concept) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT repo_id IF NOT EXISTS FOR (n:Repo) REQUIRE n.id IS UNIQUE",
]

# B-tree indexes on hot filter properties
BTREE_INDEXES = [
    "CREATE INDEX node_repo IF NOT EXISTS FOR (n:CodeNode) ON (n.repo)",
    "CREATE INDEX node_filepath IF NOT EXISTS FOR (n:CodeNode) ON (n.filePath)",
    "CREATE INDEX node_last_seen IF NOT EXISTS FOR (n:CodeNode) ON (n.last_seen_commit)",
    "CREATE INDEX doc_repo IF NOT EXISTS FOR (n:DocNode) ON (n.repo)",
]

# Full-text indexes for keyword search.
# ``nameTokens`` is a computed property that splits camelCase / PascalCase /
# snake_case identifiers into individual tokens so that searching for
# "service" matches "ClientService", "UserService", etc.
FULLTEXT_INDEXES = [
    """
    CREATE FULLTEXT INDEX code_search IF NOT EXISTS
      FOR (n:CodeNode) ON EACH [n.name, n.nameTokens, n.summary, n.signature]
    """,
    """
    CREATE FULLTEXT INDEX doc_search IF NOT EXISTS
      FOR (n:DocNode) ON EACH [n.name, n.summary, n.content, n.heading]
    """,
]

# One-time data-fix migrations run after normal index creation.
# All queries must be idempotent (safe to re-run).
DATA_FIX_QUERIES = [
    # Normalise layer='' (empty string) to NULL so IS NULL filters work correctly.
    "MATCH (n:CodeNode) WHERE n.layer = '' SET n.layer = NULL",
    # Backfill nameTokens for any nodes ingested before this property was added.
    # The apoc split is approximate; exact splits happen in Python during ingest.
    # This keeps the index searchable after a bootstrap without full re-ingest.
    r"""
    MATCH (n:CodeNode) WHERE n.nameTokens IS NULL AND n.name IS NOT NULL
    WITH n,
         toLower(
           apoc.text.regreplace(
             apoc.text.regreplace(n.name, '([a-z])([A-Z])', '$1 $2'),
             '([A-Z]+)([A-Z][a-z])', '$1 $2'
           )
         ) AS tokens
    SET n.nameTokens = tokens
    """,
]

# Vector indexes (Neo4j 5.11+) — dimensions configurable
# These will be created by create_vector_indexes() after determining dimensions
VECTOR_INDEX_TEMPLATES = {
    "code_embeddings": {
        "label": "CodeNode",
        "property": "summary_embedding",
        "similarity": "cosine",
    },
    "doc_embeddings": {
        "label": "DocNode",
        "property": "summary_embedding",
        "similarity": "cosine",
    },
}


async def apply_constraints(client: Neo4jClient) -> None:
    """Apply all uniqueness constraints."""
    for constraint in CONSTRAINTS:
        try:
            await client.execute_query(constraint)
            logger.info(f"Applied constraint: {constraint[:50]}...")
        except Exception as e:
            logger.warning(f"Constraint application failed: {e}")


async def apply_btree_indexes(client: Neo4jClient) -> None:
    """Apply B-tree indexes."""
    for index in BTREE_INDEXES:
        try:
            await client.execute_query(index)
            logger.info(f"Applied index: {index[:50]}...")
        except Exception as e:
            logger.warning(f"Index application failed: {e}")


async def apply_fulltext_indexes(client: Neo4jClient) -> None:
    """Apply full-text indexes.

    If the ``code_search`` index exists but was created WITHOUT ``nameTokens``
    (i.e. the old schema), it is dropped and recreated so camelCase splitting
    works correctly.
    """
    # Check if code_search exists and whether it already includes nameTokens
    try:
        existing = await client.execute_query(
            "SHOW FULLTEXT INDEXES WHERE name = 'code_search'", {}
        )
        if existing:
            props = existing[0].get("properties") or []
            if "nameTokens" not in props:
                logger.info("Recreating code_search index to add nameTokens support")
                await client.execute_query("DROP INDEX code_search IF EXISTS", {})
    except Exception as e:
        logger.warning(f"Could not check existing fulltext index: {e}")

    for index in FULLTEXT_INDEXES:
        try:
            await client.execute_query(index)
            logger.info(f"Applied full-text index: {index[:50]}...")
        except Exception as e:
            logger.warning(f"Full-text index application failed: {e}")


async def apply_data_fixes(client: Neo4jClient) -> None:
    """Run one-time data normalisation queries.

    These are idempotent — re-running them is safe.
    """
    for query in DATA_FIX_QUERIES:
        try:
            await client.execute_query(query)
            logger.info(f"Applied data fix: {query[:80]}...")
        except Exception as e:
            logger.warning(f"Data fix failed: {e}")


async def create_vector_indexes(
    client: Neo4jClient, dimensions: int = 384, similarity: str = "cosine"
) -> None:
    """
    Create or recreate vector indexes.

    Args:
        client: Neo4j client.
        dimensions: Embedding dimensions (e.g., 384 for bge-small).
        similarity: Similarity function (cosine, euclidean, etc.).
    """
    for index_name, config in VECTOR_INDEX_TEMPLATES.items():
        query = f"""
        CREATE VECTOR INDEX {index_name} IF NOT EXISTS
          FOR (n:{config['label']}) ON (n.{config['property']})
          OPTIONS {{indexConfig: {{`vector.dimensions`: {dimensions}, `vector.similarity_function`: '{similarity}'}}}}
        """
        try:
            await client.execute_query(query)
            logger.info(f"Created vector index: {index_name} (dim={dimensions})")
        except Exception as e:
            logger.warning(f"Vector index creation failed: {e}")


async def run_migrations(settings: Settings) -> None:
    """
    Run all migrations.

    Args:
        settings: Application settings.
    """
    client = Neo4jClient(settings.neo4j)
    await client.connect()

    try:
        logger.info("Starting migrations...")

        logger.info("Applying uniqueness constraints...")
        await apply_constraints(client)

        logger.info("Applying B-tree indexes...")
        await apply_btree_indexes(client)

        logger.info("Applying full-text indexes...")
        await apply_fulltext_indexes(client)

        dimensions = settings.embedding.dimensions
        logger.info(f"Creating vector indexes (dimensions={dimensions})...")
        await create_vector_indexes(client, dimensions=dimensions)

        logger.info("Applying data fixes...")
        await apply_data_fixes(client)

        # Health check
        health = await client.health_check()
        if health:
            logger.info("✅ Migrations completed successfully")
        else:
            logger.error("❌ Health check failed after migrations")

    finally:
        await client.close()


async def main() -> None:
    """CLI entry point for migrations."""
    from code_kg.config import Settings
    from code_kg.logging import configure_logging

    settings = Settings()
    configure_logging(settings)

    await run_migrations(settings)


if __name__ == "__main__":
    asyncio.run(main())
