"""Unit tests for S6.3 — RENAMED_FROM edges and targeted CALLS re-resolution."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from code_kg.domain.models import RawEdge, NormalizedNode
from code_kg.ingestion.upsert import (
    resolve_calls_edges_for_files,
    soft_delete_file_nodes,
    upsert_renamed_from_edge,
)


# ── soft_delete_file_nodes ────────────────────────────────────────────────────


class TestSoftDeleteFileNodes:
    async def test_calls_query_with_correct_params(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[{"cnt": 5}])
        cnt = await soft_delete_file_nodes(client, "myrepo", "Services/ClientService.cs")
        assert cnt == 5
        call_args = client.execute_query.call_args
        assert call_args[0][1]["repo"] == "myrepo"
        assert call_args[0][1]["file_path"] == "Services/ClientService.cs"

    async def test_returns_zero_on_no_matches(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[{"cnt": 0}])
        cnt = await soft_delete_file_nodes(client, "repo", "nonexistent.cs")
        assert cnt == 0

    async def test_returns_zero_on_error(self):
        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=Exception("query failed"))
        cnt = await soft_delete_file_nodes(client, "repo", "file.cs")
        assert cnt == 0  # graceful degradation


# ── upsert_renamed_from_edge ──────────────────────────────────────────────────


class TestUpsertRenamedFromEdge:
    async def test_returns_true_on_success(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[{"r": {}}])
        ok = await upsert_renamed_from_edge(
            client, "repo", "old/path.cs", "new/path.cs", "abc123"
        )
        assert ok is True

    async def test_returns_false_when_no_rows(self):
        """If one of the File nodes doesn't exist, MATCH returns nothing."""
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[])
        ok = await upsert_renamed_from_edge(
            client, "repo", "ghost.cs", "new.cs", "abc123"
        )
        assert ok is False

    async def test_returns_false_on_exception(self):
        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=Exception("neo4j error"))
        ok = await upsert_renamed_from_edge(client, "repo", "a.cs", "b.cs", "sha")
        assert ok is False


# ── resolve_calls_edges_for_files ─────────────────────────────────────────────


def _make_calls_edge(from_file: str, from_method: str, call_name: str) -> RawEdge:
    return RawEdge(
        type="CALLS",
        from_id="<calls-placeholder>",
        to_id="<calls-placeholder>",
        weight=0.8,
        metadata={
            "call_name": call_name,
            "from_method": from_method,
            "from_file": from_file,
        },
    )


def _make_node(type_: str, name: str, file_path: str, repo: str = "repo") -> NormalizedNode:
    return NormalizedNode(
        id=f"function:{repo}:{file_path}:{name}",
        type=type_,
        name=name,
        repo=repo,
        file_path=file_path,
    )


class TestResolveCallsEdgesForFiles:
    def test_full_resolution_when_no_changed_files(self):
        edges = [
            _make_calls_edge("a.cs", "GetFoo", "GetBar"),
            _make_calls_edge("b.cs", "DoThing", "GetFoo"),
        ]
        nodes = [
            _make_node("function", "GetFoo", "a.cs"),
            _make_node("function", "GetBar", "a.cs"),
            _make_node("function", "DoThing", "b.cs"),
        ]
        resolved = resolve_calls_edges_for_files(edges, "repo", nodes, changed_files=None)
        from_ids = {e.from_id for e, _, _ in resolved}
        # Both files should be resolved
        assert any("a.cs" in fid for fid in from_ids)
        assert any("b.cs" in fid for fid in from_ids)

    def test_scoped_to_changed_file_only(self):
        edges = [
            _make_calls_edge("a.cs", "GetFoo", "GetBar"),
            _make_calls_edge("b.cs", "DoThing", "GetFoo"),
        ]
        nodes = [
            _make_node("function", "GetFoo", "a.cs"),
            _make_node("function", "GetBar", "a.cs"),
            _make_node("function", "DoThing", "b.cs"),
        ]
        resolved = resolve_calls_edges_for_files(
            edges, "repo", nodes, changed_files={"a.cs"}
        )
        from_ids = {e.from_id for e, _, _ in resolved}
        # Only edges from a.cs should appear
        assert all("a.cs" in fid for fid in from_ids)
        assert not any("b.cs" in fid for fid in from_ids)

    def test_empty_changed_files_falls_back_to_full(self):
        edges = [_make_calls_edge("a.cs", "Foo", "Bar")]
        nodes = [
            _make_node("function", "Foo", "a.cs"),
            _make_node("function", "Bar", "a.cs"),
        ]
        resolved_empty = resolve_calls_edges_for_files(edges, "repo", nodes, set())
        resolved_none = resolve_calls_edges_for_files(edges, "repo", nodes, None)
        # Both should produce the same result
        assert len(resolved_empty) == len(resolved_none)

    def test_cross_file_call_resolved_from_changed_file(self):
        """A call FROM changed file TO unchanged file should still be resolved."""
        edges = [_make_calls_edge("changed.cs", "A", "B")]
        nodes = [
            _make_node("function", "A", "changed.cs"),
            _make_node("function", "B", "unchanged.cs"),  # target is in unchanged file
        ]
        resolved = resolve_calls_edges_for_files(
            edges, "repo", nodes, changed_files={"changed.cs"}
        )
        assert len(resolved) > 0
        _, from_id, to_id = resolved[0]
        assert "changed.cs" in from_id
        assert "unchanged.cs" in to_id
