"""Unit tests for the tests mapper (S3.4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_kg.ingestion.tests_mapper import (
    TestsMapperResult,
    _base_name_from_path,
    _is_test_path,
    _mirror_path,
    _strategy_filename,
    _strategy_path_mirror,
    _strip_test_suffix,
    map_tests,
)


# ── helper function tests ─────────────────────────────────────────────────────


class TestHelpers:
    def test_is_test_path_cs(self):
        assert _is_test_path("DevOnHire.Tests/Tests/CandidateServiceTests.cs") is True

    def test_is_test_path_ts_spec(self):
        assert _is_test_path("src/app/feature/candidate.service.spec.ts") is True

    def test_is_test_path_production(self):
        assert _is_test_path("src/Services/CandidateService.cs") is False

    def test_strip_test_suffix_cs(self):
        assert _strip_test_suffix("CandidateServiceTests") == "CandidateService"
        assert _strip_test_suffix("CandidateServiceTest") == "CandidateService"

    def test_strip_test_suffix_ts(self):
        assert _strip_test_suffix("candidate.service.spec") == "candidate.service"
        assert _strip_test_suffix("candidate.service.test") == "candidate.service"

    def test_strip_test_suffix_no_suffix(self):
        assert _strip_test_suffix("CandidateService") == "CandidateService"

    def test_base_name_from_path(self):
        assert _base_name_from_path("Tests/CandidateServiceTests.cs") == "CandidateService"
        assert _base_name_from_path("src/Services/CandidateService.cs") == "CandidateService"

    def test_mirror_path_test_to_src(self):
        result = _mirror_path("tests/Services/CandidateServiceTest.cs")
        assert result is not None
        assert "src" in result
        assert "Test" not in result

    def test_mirror_path_spec_ts(self):
        result = _mirror_path("tests/services/candidate.service.spec.ts")
        assert result is not None
        assert ".spec" not in result

    def test_mirror_path_no_test_dir(self):
        assert _mirror_path("src/Services/CandidateService.cs") is None


# ── strategy: filename heuristic ──────────────────────────────────────────────


class TestStrategyFilename:
    def test_matches_cs_test_to_service(self):
        test_nodes = [
            {"id": "test:1", "name": "GetClientTest", "filePath": "Tests/ClientServiceTests.cs"}
        ]
        prod_nodes = [
            {"id": "prod:1", "name": "GetClient", "filePath": "Services/ClientService.cs"}
        ]
        result = _strategy_filename(test_nodes, prod_nodes)
        assert ("test:1", "prod:1") in result
        assert result[("test:1", "prod:1")] == pytest.approx(0.7)

    def test_matches_ts_spec(self):
        test_nodes = [
            {"id": "test:2", "name": "describe", "filePath": "app/candidate.service.spec.ts"}
        ]
        prod_nodes = [
            {"id": "prod:2", "name": "CandidateService",
             "filePath": "app/candidate.service.ts"}
        ]
        result = _strategy_filename(test_nodes, prod_nodes)
        assert ("test:2", "prod:2") in result

    def test_no_match_different_names(self):
        test_nodes = [
            {"id": "test:3", "name": "X", "filePath": "Tests/FooTests.cs"}
        ]
        prod_nodes = [
            {"id": "prod:3", "name": "Bar", "filePath": "Services/BarService.cs"}
        ]
        result = _strategy_filename(test_nodes, prod_nodes)
        assert len(result) == 0

    def test_no_self_match(self):
        """Test nodes should not be matched against themselves."""
        test_nodes = [{"id": "test:1", "name": "T", "filePath": "Tests/TTests.cs"}]
        prod_nodes = []
        result = _strategy_filename(test_nodes, prod_nodes)
        assert len(result) == 0


# ── strategy: path mirroring ──────────────────────────────────────────────────


class TestStrategyPathMirror:
    def test_mirrors_test_to_src(self):
        test_nodes = [
            {"id": "test:1", "name": "X", "filePath": "tests/Services/CandidateTest.cs"}
        ]
        prod_by_path = {"src/Services/Candidate.cs": ["prod:1"]}
        result = _strategy_path_mirror(test_nodes, prod_by_path)
        assert ("test:1", "prod:1") in result
        assert result[("test:1", "prod:1")] == pytest.approx(0.6)

    def test_no_mirror_for_prod_path(self):
        test_nodes = [
            {"id": "test:2", "name": "Y", "filePath": "src/Services/Foo.cs"}
        ]
        result = _strategy_path_mirror(test_nodes, {})
        assert len(result) == 0


# ── map_tests integration (mocked Neo4j) ─────────────────────────────────────


class TestMapTests:
    async def test_no_test_nodes_returns_empty(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[])
        result = await map_tests(client, "my-repo")
        assert result.edges_written == 0
        assert result.edges_skipped == 0
        assert result.errors == []

    async def test_emits_edge_from_calls_strategy(self):
        test_node = {
            "id": "func:repo:Tests/ClientTest.cs:GetClient_Success",
            "name": "GetClient_Success",
            "filePath": "Tests/ClientServiceTests.cs",
            "labels": ["Function", "CodeNode"],
        }
        prod_node = {
            "id": "func:repo:Services/ClientService.cs:GetClient",
            "name": "GetClient",
            "filePath": "Services/ClientService.cs",
            "labels": ["Function", "CodeNode"],
        }
        client = MagicMock()
        # Sequence: LOAD_TEST_NODES, LOAD_ALL_CODE_NODES_FOR_REPO, LOAD_CALLS_FROM_TEST
        client.execute_query = AsyncMock(side_effect=[
            [test_node],                # LOAD_TEST_NODES
            [test_node, prod_node],     # LOAD_ALL_CODE_NODES_FOR_REPO
            [prod_node],                # LOAD_CALLS_FROM_TEST
            [{"r": {}}],               # upsert_edge (TESTS MERGE)
        ])

        with patch("code_kg.ingestion.tests_mapper.upsert_edge",
                   new=AsyncMock(return_value=True)):
            result = await map_tests(client, "repo")

        assert result.edges_written == 1
        assert result.errors == []

    async def test_deduplicates_edges_from_multiple_strategies(self):
        """Same (from, to) pair from calls + filename → only one edge emitted."""
        test_node = {
            "id": "t:1", "name": "TestGetClient",
            "filePath": "Tests/ClientServiceTests.cs",
        }
        prod_node = {
            "id": "p:1", "name": "GetClient",
            "filePath": "Services/ClientService.cs",
        }
        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=[
            [test_node],            # LOAD_TEST_NODES
            [test_node, prod_node], # LOAD_ALL_CODE_NODES_FOR_REPO
            [prod_node],            # LOAD_CALLS_FROM_TEST — strategy 1
        ])

        emitted: list = []

        async def fake_upsert(client, edge, from_id, to_id):
            emitted.append((from_id, to_id, edge.weight))
            return True

        with patch("code_kg.ingestion.tests_mapper.upsert_edge", side_effect=fake_upsert):
            result = await map_tests(client, "repo")

        # Both strategies fire but deduplication keeps only one pair
        # Weight should be the maximum (0.9 from CALLS)
        pairs = {(f, t) for f, t, _ in emitted}
        assert len(pairs) == 1
        weights = [w for _, _, w in emitted]
        assert max(weights) == pytest.approx(0.9)
