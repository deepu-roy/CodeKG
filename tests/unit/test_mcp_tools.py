"""Tests for MCP read tools (mocked Neo4j)."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from code_kg.domain.models import (
    FindNodesInput,
    GetNodeInput,
    ImpactAnalysisInput,
)
from code_kg.mcp.tools.read import (
    find_nodes,
    get_node,
    impact_analysis,
    find_tests_for,
    semantic_search,
    trace_call_chain,
)
from code_kg.mcp.shaping import (
    shape_node_for_list,
    shape_node_detail,
    shape_neighbor,
    reciprocal_rank_fusion,
)


# ──────────────────────────────────────────────────────────────────────────────
# Shaping helpers
# ──────────────────────────────────────────────────────────────────────────────

_SAMPLE_ROW = {
    "id": "class:myapp:src/svc.ts:UserService",
    "labels": ["Class", "CodeNode"],
    "name": "UserService",
    "repo": "myapp",
    "filePath": "src/svc.ts",
    "summary": "A service class for user management.",
    "tags": ["service", "user"],
    "layer": "service",
    "score": 0.95,
}


class TestShaping:
    def test_shape_node_for_list_truncates_summary(self):
        row = dict(_SAMPLE_ROW)
        row["summary"] = "x" * 400
        shaped = shape_node_for_list(row)
        assert len(shaped["summary"]) <= 281  # 280 + "…"
        assert shaped["summary"].endswith("…")

    def test_shape_node_for_list_picks_primary_label(self):
        shaped = shape_node_for_list(_SAMPLE_ROW)
        assert shaped["type"] == "Class"  # not "CodeNode"

    def test_shape_node_for_list_omits_code_snippet_by_default(self):
        row = dict(_SAMPLE_ROW, codeSnippet="class UserService {...}")
        shaped = shape_node_for_list(row)
        assert "codeSnippet" not in shaped

    def test_shape_node_for_list_omits_code_snippet_even_when_requested(self):
        # codeSnippet is no longer stored in the graph; include_code=True is a no-op
        row = dict(_SAMPLE_ROW, codeSnippet="class UserService {...}")
        shaped = shape_node_for_list(row, include_code=True)
        assert "codeSnippet" not in shaped

    def test_shape_node_detail_allows_longer_summary(self):
        row = dict(_SAMPLE_ROW)
        row["summary"] = "y" * 2100
        shaped = shape_node_detail(row)
        assert len(shaped["summary"]) <= 2001  # 2000 + "…"

    def test_shape_neighbor(self):
        nbr_row = {
            "id": "function:myapp:src/repo.ts:getUser",
            "labels": ["Function", "CodeNode"],
            "name": "getUser",
            "edge_type": "CALLS",
            "weight": 1.0,
        }
        shaped = shape_neighbor(nbr_row)
        assert shaped["edge_type"] == "CALLS"
        assert shaped["type"] == "Function"


class TestRRF:
    def test_merges_two_lists(self):
        a = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        b = [{"id": "2"}, {"id": "4"}, {"id": "1"}]
        merged = reciprocal_rank_fusion(a, b, limit=4)
        # "2" appears in both; should rank highly
        ids = [r["id"] for r in merged]
        assert "2" in ids
        assert "1" in ids

    def test_respects_limit(self):
        a = [{"id": str(i)} for i in range(20)]
        b = [{"id": str(i)} for i in range(20, 40)]
        merged = reciprocal_rank_fusion(a, b, limit=5)
        assert len(merged) == 5

    def test_empty_lists(self):
        assert reciprocal_rank_fusion([], []) == []

    def test_single_list(self):
        a = [{"id": "x"}, {"id": "y"}]
        merged = reciprocal_rank_fusion(a, [], limit=2)
        assert len(merged) == 2


# ──────────────────────────────────────────────────────────────────────────────
# find_nodes
# ──────────────────────────────────────────────────────────────────────────────

def _make_client(rows):
    client = MagicMock()
    client.execute_query = AsyncMock(return_value=rows)
    return client


class TestFindNodes:
    async def test_returns_nodes_from_fulltext(self):
        rows = [dict(_SAMPLE_ROW)]
        client = _make_client(rows)
        inp = FindNodesInput(query="user service", use_semantic=False)
        result = await find_nodes(client, inp)
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "UserService"

    async def test_empty_result_returns_empty_list(self):
        client = _make_client([])
        inp = FindNodesInput(query="nonexistent", use_semantic=False)
        result = await find_nodes(client, inp)
        assert result.nodes == []
        assert result.total_matched == 0

    async def test_semantic_search_merges_with_fulltext(self):
        rows = [dict(_SAMPLE_ROW)]
        client = _make_client(rows)
        inp = FindNodesInput(query="user management", use_semantic=True)
        embedding = [0.1] * 384
        result = await find_nodes(client, inp, query_embedding=embedding)
        assert len(result.nodes) >= 1

    async def test_skip_semantic_when_no_embedding(self):
        rows = [dict(_SAMPLE_ROW)]
        client = _make_client(rows)
        inp = FindNodesInput(query="test", use_semantic=True)
        # No embedding → falls back to full-text only
        result = await find_nodes(client, inp, query_embedding=None)
        assert len(result.nodes) >= 0  # should not crash


# ──────────────────────────────────────────────────────────────────────────────
# get_node
# ──────────────────────────────────────────────────────────────────────────────

class TestGetNode:
    async def test_returns_node_detail(self):
        detail_row = dict(_SAMPLE_ROW, signature="class UserService {}", lineRange=[1, 50])
        nbr_row = {
            "id": "function:myapp:src/svc.ts:getUser",
            "labels": ["Function", "CodeNode"],
            "name": "getUser",
            "edge_type": "DEFINES",
            "weight": 1.0,
        }
        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=[
            [detail_row],   # GET_NODE_BY_ID
            [nbr_row],       # GET_NEIGHBORS
        ])
        inp = GetNodeInput(id=_SAMPLE_ROW["id"])
        result = await get_node(client, inp)
        assert result is not None
        assert result.name == "UserService"
        assert len(result.neighbors) == 1
        assert result.neighbors[0].edge_type == "DEFINES"

    async def test_returns_none_for_missing_node(self):
        client = _make_client([])
        inp = GetNodeInput(id="nonexistent:id")
        result = await get_node(client, inp)
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# impact_analysis
# ──────────────────────────────────────────────────────────────────────────────

_CALLER_ROW = {
    "id": "function:myapp:src/ctrl.ts:handleRequest",
    "labels": ["Function", "CodeNode"],
    "name": "handleRequest",
    "repo": "myapp",
    "filePath": "src/ctrl.ts",
    "summary": "HTTP handler",
    "tags": [],
    "layer": "controller",
    "depth": 1,
    "path_ids": ["function:myapp:src/ctrl.ts:handleRequest", _SAMPLE_ROW["id"]],
}


class TestImpactAnalysis:
    async def test_returns_impact_output(self):
        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=[
            [_SAMPLE_ROW],      # GET_NODE_BY_ID
            [_CALLER_ROW],      # IMPACT_UPSTREAM
            [],                 # IMPACT_DOWNSTREAM
            [],                 # IMPACT_TESTS
            [],                 # IMPACT_DOCS
        ])
        inp = ImpactAnalysisInput(id=_SAMPLE_ROW["id"])
        result = await impact_analysis(client, inp)
        assert result is not None
        assert result.target.name == "UserService"
        assert len(result.upstream) == 1
        assert result.upstream[0].node.name == "handleRequest"
        assert result.layer_breakdown.get("controller") == 1

    async def test_returns_none_for_missing_node(self):
        client = _make_client([])
        inp = ImpactAnalysisInput(id="missing:id")
        result = await impact_analysis(client, inp)
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# find_tests_for
# ──────────────────────────────────────────────────────────────────────────────

class TestFindTestsFor:
    async def test_returns_test_nodes(self):
        test_row = {
            "id": "function:myapp:tests/svc.spec.ts:testGetUser",
            "labels": ["Function", "CodeNode"],
            "name": "testGetUser",
            "repo": "myapp",
            "filePath": "tests/svc.spec.ts",
            "summary": "Tests getUser",
            "tags": ["test"],
            "layer": None,
            "score": 1.0,
        }
        client = _make_client([test_row])
        result = await find_tests_for(client, _SAMPLE_ROW["id"])
        assert len(result) == 1
        assert result[0].name == "testGetUser"


# ──────────────────────────────────────────────────────────────────────────────
# trace_call_chain
# ──────────────────────────────────────────────────────────────────────────────

class TestTraceCallChain:
    async def test_returns_chain(self):
        chain_row = {
            "chain": [
                {"id": "a", "name": "methodA", "type": "Function"},
                {"id": "b", "name": "methodB", "type": "Function"},
            ],
            "depth": 1,
        }
        client = _make_client([chain_row])
        result = await trace_call_chain(client, "a", "b")
        assert result is not None
        assert result["depth"] == 1
        assert len(result["chain"]) == 2

    async def test_returns_none_when_no_path(self):
        client = _make_client([])
        result = await trace_call_chain(client, "a", "b")
        assert result is None
