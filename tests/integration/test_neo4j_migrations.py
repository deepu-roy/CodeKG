"""Integration tests for Neo4j migrations.

These tests require a running Neo4j instance. Set NEO4J__* env vars or use .env.local.
Run with: pytest tests/integration/test_neo4j_migrations.py -v
"""

import asyncio
import os

import pytest

from code_kg.config import Settings
from code_kg.graph.client import Neo4jClient
from code_kg.graph.migrations import run_migrations


@pytest.fixture
async def neo4j_client():
    """Connect to Neo4j for testing."""
    try:
        settings = Settings()
    except Exception:
        pytest.skip("Neo4j settings not configured")

    client = Neo4jClient(settings.neo4j)
    await client.connect()

    # Health check
    health = await client.health_check()
    if not health:
        pytest.skip("Neo4j is not reachable")

    # Ensure migrations have been run
    await run_migrations(settings)

    yield client

    # Cleanup
    await client.close()


@pytest.mark.asyncio
async def test_neo4j_connection(neo4j_client):
    """Test that we can connect to Neo4j and execute a simple query."""
    result = await neo4j_client.execute_query("RETURN 1 AS test")
    assert len(result) == 1
    assert result[0]["test"] == 1


@pytest.mark.asyncio
async def test_neo4j_health_check(neo4j_client):
    """Test the health check method."""
    health = await neo4j_client.health_check()
    assert health is True


@pytest.mark.asyncio
async def test_constraints_created(neo4j_client):
    """Verify that uniqueness constraints were created by migrations."""
    # Query existing constraints
    result = await neo4j_client.execute_query("SHOW CONSTRAINTS")

    constraint_names = {r.get("name", "") for r in result if r.get("type") == "UNIQUENESS"}
    expected_constraints = {
        "file_id",
        "class_id",
        "function_id",
        "method_id",
        "endpoint_id",
        "document_id",
        "section_id",
        "repo_id",
    }

    # Check that at least some constraints exist
    found = expected_constraints & constraint_names
    assert len(found) > 0, f"Expected constraints not found. Found: {constraint_names}"


@pytest.mark.asyncio
async def test_btree_indexes_created(neo4j_client):
    """Verify that B-tree indexes were created."""
    result = await neo4j_client.execute_query("SHOW INDEXES")

    # In Neo4j 5.x, B-tree indexes are represented as 'RANGE' type
    index_names = {r.get("name", "") for r in result if r.get("type") == "RANGE"}
    expected_indexes = {"node_repo", "node_filepath", "node_last_seen", "doc_repo"}

    found = expected_indexes & index_names
    assert len(found) > 0, f"Expected indexes not found. Found: {index_names}"


@pytest.mark.asyncio
async def test_migrations_idempotent(neo4j_client):
    """Verify that migrations can be run multiple times safely."""
    settings = Settings()

    # Run migrations twice
    await run_migrations(settings)
    await run_migrations(settings)

    # If we get here without exceptions, migrations are idempotent
    assert True


@pytest.mark.asyncio
async def test_write_query(neo4j_client):
    """Test that we can write to the database."""
    query = """
    CREATE (n:TestNode {id: "test:001", name: "Test Node", repo: "test-repo"})
    RETURN n.id AS id
    """

    result = await neo4j_client.execute_query(query)
    assert len(result) == 1
    assert result[0]["id"] == "test:001"

    # Cleanup
    await neo4j_client.execute_query("MATCH (n:TestNode {id: 'test:001'}) DELETE n")


if __name__ == "__main__":
    # Quick manual test
    async def main():
        try:
            settings = Settings()
            client = Neo4jClient(settings.neo4j)
            await client.connect()

            print("✅ Connected to Neo4j")
            health = await client.health_check()
            print(f"✅ Health check: {health}")

            await run_migrations(settings)
            print("✅ Migrations completed")

            await client.close()
        except Exception as e:
            print(f"❌ Error: {e}")

    asyncio.run(main())
