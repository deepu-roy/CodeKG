"""Tests for the enrichment pipeline and providers."""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_kg.domain.models import NormalizedNode, SummaryRequest, SummaryResponse
from code_kg.ingestion.enrichment import (
    _build_summary_request,
    enrich_batch,
    enrich_node,
)
from code_kg.providers.summary.prompts import build_prompt


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_node(**kwargs) -> NormalizedNode:
    defaults = dict(
        id="node::test::1",
        type="class",
        name="UserService",
        repo="my-app",
        language="typescript",
        file_path="src/services/user.ts",
        code_snippet="class UserService { getUser() { return null; } }",
    )
    defaults.update(kwargs)
    return NormalizedNode(**defaults)


class MockSummaryProvider:
    async def summarise(self, request: SummaryRequest) -> SummaryResponse:
        return SummaryResponse(
            summary=f"A {request.node_type} called {request.name}.",
            tags=["test", request.node_type],
            complexity="simple",
        )


class MockEmbeddingProvider:
    @property
    def dimensions(self) -> int:
        return 384

    async def embed(self, text: str) -> list[float]:
        return [0.1] * 384

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


# ──────────────────────────────────────────────────────────────────────────────
# _build_summary_request
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildSummaryRequest:
    def test_class_node_returns_request(self):
        node = _make_node(type="class")
        req = _build_summary_request(node)
        assert req is not None
        assert req.node_type == "class"
        assert req.name == "UserService"
        assert req.language == "typescript"

    def test_function_node_includes_signature(self):
        node = _make_node(type="function", signature="function getUser(): User")
        req = _build_summary_request(node)
        assert req is not None
        assert "getUser" in req.code_or_text

    def test_unsupported_type_returns_none(self):
        node = _make_node(type="file", name="main.ts", code_snippet=None, signature=None)
        # file IS in _SUMMARISABLE_TYPES, so let's test a real unsupported type
        # by temporarily checking that non-summarisable types return None
        # We'll test with 'module' which is not in the set
        node2 = _make_node(type="module")
        req = _build_summary_request(node2)
        assert req is None

    def test_file_node_returns_request(self):
        node = _make_node(type="file", name="main.ts")
        req = _build_summary_request(node)
        assert req is not None
        assert req.node_type == "file"

    def test_interface_node_returns_request(self):
        node = _make_node(type="interface", name="IUserRepo")
        req = _build_summary_request(node)
        assert req is not None
        assert req.node_type == "interface"

    def test_context_includes_file_path(self):
        node = _make_node(file_path="src/foo/bar.ts")
        req = _build_summary_request(node)
        assert req is not None
        assert "src/foo/bar.ts" in (req.context or "")


# ──────────────────────────────────────────────────────────────────────────────
# enrich_node
# ──────────────────────────────────────────────────────────────────────────────

class TestEnrichNode:
    async def test_enrich_adds_summary_and_embedding(self):
        node = _make_node()
        enriched = await enrich_node(node, MockSummaryProvider(), MockEmbeddingProvider())
        assert enriched.summary == "A class called UserService."
        assert "class" in enriched.tags
        assert enriched.complexity == "simple"
        assert enriched.summary_embedding is not None
        assert len(enriched.summary_embedding) == 384

    async def test_enrich_skips_unsupported_type(self):
        node = _make_node(type="module")
        enriched = await enrich_node(node, MockSummaryProvider(), MockEmbeddingProvider())
        # Module is not summarisable; node returned unchanged
        assert enriched.summary is None
        assert enriched.summary_embedding is None

    async def test_enrich_handles_summary_failure(self):
        """If summary fails, embedding should still be applied."""
        class FailingSummaryProvider:
            async def summarise(self, request: SummaryRequest) -> SummaryResponse:
                raise RuntimeError("LLM down")

        node = _make_node()
        enriched = await enrich_node(node, FailingSummaryProvider(), MockEmbeddingProvider())
        assert enriched.summary is None
        assert enriched.summary_embedding is not None  # embedding still worked

    async def test_enrich_handles_embedding_failure(self):
        """If embedding fails, summary should still be applied."""
        class FailingEmbeddingProvider:
            @property
            def dimensions(self) -> int:
                return 384

            async def embed(self, text: str) -> list[float]:
                raise RuntimeError("GPU OOM")

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("GPU OOM")

        node = _make_node()
        enriched = await enrich_node(node, MockSummaryProvider(), FailingEmbeddingProvider())
        assert enriched.summary is not None
        assert enriched.summary_embedding is None  # embedding failed

    async def test_enrich_preserves_existing_fields(self):
        node = _make_node(layer="service", repo="svc")
        enriched = await enrich_node(node, MockSummaryProvider(), MockEmbeddingProvider())
        assert enriched.layer == "service"
        assert enriched.repo == "svc"


# ──────────────────────────────────────────────────────────────────────────────
# enrich_batch
# ──────────────────────────────────────────────────────────────────────────────

class TestEnrichBatch:
    async def test_batch_returns_counts(self):
        nodes = [_make_node(id=f"node::{i}", name=f"Service{i}") for i in range(3)]

        mock_client = AsyncMock()
        mock_client.execute_query = AsyncMock(return_value=[{"n": {"id": n.id}} for n in nodes])

        enriched, written = await enrich_batch(
            nodes,
            summary_provider=MockSummaryProvider(),
            embedding_provider=MockEmbeddingProvider(),
            client=mock_client,
            max_concurrent=2,
        )
        assert enriched == 3
        assert written == 3

    async def test_empty_batch_returns_zeros(self):
        mock_client = AsyncMock()
        enriched, written = await enrich_batch(
            [],
            summary_provider=MockSummaryProvider(),
            embedding_provider=MockEmbeddingProvider(),
            client=mock_client,
        )
        assert enriched == 0
        assert written == 0


# ──────────────────────────────────────────────────────────────────────────────
# build_prompt
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_prompt_contains_node_name(self):
        req = SummaryRequest(
            node_id="x",
            node_type="class",
            name="MyClass",
            language="typescript",
            code_or_text="class MyClass {}",
        )
        system, user = build_prompt(req)
        assert "MyClass" in user
        assert "class" in user.lower()
        assert "typescript" in user.lower()

    def test_system_prompt_is_non_empty(self):
        req = SummaryRequest(
            node_id="x",
            node_type="function",
            name="doWork",
            language="java",
            code_or_text="void doWork() {}",
        )
        system, _ = build_prompt(req)
        assert len(system) > 20

    def test_long_code_is_truncated(self):
        long_code = "x" * 10_000
        req = SummaryRequest(
            node_id="x",
            node_type="class",
            name="Big",
            language="python",
            code_or_text=long_code,
        )
        _, user = build_prompt(req)
        # Template hard-caps at 4000 chars
        assert long_code not in user
        assert len(user) < 5000 + 500  # template overhead is < 500 chars
