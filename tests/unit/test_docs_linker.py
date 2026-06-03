"""Unit tests for the docs linker (S4.3)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_kg.ingestion.docs_linker import (
    DocsLinkerResult,
    _MIN_CONFIDENCE,
    classify_with_llm,
    find_candidates_by_name,
    find_candidates_by_vector,
    link_docs,
)


# ── find_candidates_by_name ───────────────────────────────────────────────────


class TestFindCandidatesByName:
    def test_exact_match(self):
        text = "The UserService handles user authentication and registration."
        nodes = [
            {"id": "n:1", "name": "UserService", "filePath": "svc.ts"},
            {"id": "n:2", "name": "CandidateService", "filePath": "cand.ts"},
        ]
        result = find_candidates_by_name(text, nodes)
        assert len(result) == 1
        assert result[0]["id"] == "n:1"

    def test_no_partial_word_match(self):
        """'Service' should not match 'UserService' as a whole word."""
        text = "The Service layer handles requests."
        nodes = [{"id": "n:1", "name": "UserService", "filePath": "svc.ts"}]
        result = find_candidates_by_name(text, nodes)
        assert len(result) == 0

    def test_multiple_matches(self):
        text = "CandidateService and ClientService both implement IService."
        nodes = [
            {"id": "n:1", "name": "CandidateService", "filePath": "a.cs"},
            {"id": "n:2", "name": "ClientService", "filePath": "b.cs"},
            {"id": "n:3", "name": "Unrelated", "filePath": "c.cs"},
        ]
        result = find_candidates_by_name(text, nodes)
        ids = {r["id"] for r in result}
        assert ids == {"n:1", "n:2"}

    def test_skips_short_names(self):
        """Names shorter than 3 characters should not be matched to avoid noise."""
        text = "The Id is used here."
        nodes = [{"id": "n:1", "name": "Id", "filePath": "a.ts"}]
        result = find_candidates_by_name(text, nodes)
        assert len(result) == 0

    def test_empty_text(self):
        nodes = [{"id": "n:1", "name": "Foo", "filePath": "foo.ts"}]
        assert find_candidates_by_name("", nodes) == []

    def test_empty_nodes(self):
        assert find_candidates_by_name("some text with Foo", []) == []


# ── find_candidates_by_vector ─────────────────────────────────────────────────


class TestFindCandidatesByVector:
    async def test_returns_empty_when_no_embedding(self):
        client = MagicMock()
        result = await find_candidates_by_vector(client, None, "repo")
        assert result == []

    async def test_returns_nodes_from_vector_index(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[
            {"id": "n:1", "name": "UserService", "filePath": "svc.ts",
             "labels": ["Class"], "summary": None, "tags": None, "layer": None, "score": 0.9}
        ])
        result = await find_candidates_by_vector(client, [0.1] * 384, "repo", top_k=5)
        assert len(result) == 1
        assert result[0]["id"] == "n:1"

    async def test_returns_empty_on_error(self):
        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=Exception("index not ready"))
        result = await find_candidates_by_vector(client, [0.1] * 384, "repo")
        assert result == []


# ── classify_with_llm ─────────────────────────────────────────────────────────


class TestClassifyWithLLM:
    async def test_parses_valid_json(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=json.dumps({
            "documented": [
                {"name": "CandidateService", "confidence": 0.9},
                {"name": "ClientService", "confidence": 0.4},
            ]
        }))
        candidates = [
            {"id": "n:1", "name": "CandidateService"},
            {"id": "n:2", "name": "ClientService"},
            {"id": "n:3", "name": "Unrelated"},
        ]
        result = await classify_with_llm(llm, "qwen", "section text", candidates)
        ids = {r[0] for r in result}
        assert "n:1" in ids
        assert "n:2" in ids
        assert "n:3" not in ids  # not mentioned by LLM

    async def test_filters_below_min_confidence(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=json.dumps({
            "documented": [{"name": "Foo", "confidence": 0.1}]
        }))
        candidates = [{"id": "n:1", "name": "Foo"}]
        result = await classify_with_llm(llm, "qwen", "text", candidates)
        assert result == []  # 0.1 < _MIN_CONFIDENCE (0.3)

    async def test_fallback_on_invalid_json(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="not json at all")
        candidates = [
            {"id": "n:1", "name": "Foo"},
            {"id": "n:2", "name": "Bar"},
        ]
        result = await classify_with_llm(llm, "qwen", "text", candidates)
        # Fallback: all candidates at confidence 0.5
        assert len(result) == 2
        for node_id, conf in result:
            assert conf == pytest.approx(0.5)

    async def test_empty_candidates(self):
        llm = MagicMock()
        result = await classify_with_llm(llm, "qwen", "text", [])
        assert result == []

    async def test_llm_error_returns_fallback(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("connection error"))
        candidates = [{"id": "n:1", "name": "Foo"}]
        result = await classify_with_llm(llm, "qwen", "text", candidates)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(0.5)


# ── link_docs (mocked) ────────────────────────────────────────────────────────


class TestLinkDocs:
    async def test_no_sections_returns_empty(self):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[])
        settings = MagicMock()
        settings.summary.base_url = "http://localhost:11434"
        settings.summary.model = "qwen"

        result = await link_docs(client, settings, "myrepo")
        assert result.sections_processed == 0
        assert result.edges_written == 0
        assert result.errors == []

    async def test_writes_edge_for_high_confidence_match(self):
        """Section text contains a code symbol → LLM confirms → edge written."""
        section = {
            "id": "sec:1",
            "name": "ClientService",
            "text": "ClientService handles all client CRUD operations.",
            "embedding": None,
        }
        code_node = {"id": "cls:1", "name": "ClientService", "filePath": "svc.cs"}

        client = MagicMock()
        # LOAD_SECTIONS_WITH_TEXT, LOAD_ALL_CODE_NAMES_FOR_REPO, DOCUMENTS edge upsert
        client.execute_query = AsyncMock(side_effect=[
            [section],          # LOAD_SECTIONS_WITH_TEXT
            [code_node],        # LOAD_ALL_CODE_NAMES_FOR_REPO
            [{"r": {}}],       # DOCUMENTS edge upsert
        ])
        settings = MagicMock()
        settings.summary.base_url = "http://localhost:11434"
        settings.summary.model = "qwen"

        with patch("code_kg.ingestion.docs_linker.LLMClient") as MockLLM:
            mock_instance = MagicMock()
            mock_instance.chat = AsyncMock(return_value=json.dumps({
                "documented": [{"name": "ClientService", "confidence": 0.85}]
            }))
            MockLLM.return_value = mock_instance

            result = await link_docs(client, settings, "myrepo", use_embeddings=False)

        assert result.sections_processed == 1
        assert result.edges_written == 1

    async def test_skips_low_confidence(self):
        """When LLM returns confidence below min_confidence, classify_with_llm
        filters them before link_docs sees them → 0 edges written, 0 edges skipped
        (classify_with_llm handles the filtering internally)."""
        section = {
            "id": "sec:1",
            "name": "Overview",
            "text": "This section describes the architecture.",
            "embedding": None,
        }
        code_node = {"id": "cls:1", "name": "ClientService", "filePath": "svc.cs"}

        client = MagicMock()
        client.execute_query = AsyncMock(side_effect=[
            [section],
            [code_node],
        ])
        settings = MagicMock()
        settings.summary.base_url = "http://localhost:11434"
        settings.summary.model = "qwen"

        with patch("code_kg.ingestion.docs_linker.LLMClient") as MockLLM:
            mock_instance = MagicMock()
            # confidence 0.1 < _MIN_CONFIDENCE (0.3) → classify_with_llm returns []
            mock_instance.chat = AsyncMock(return_value=json.dumps({
                "documented": [{"name": "ClientService", "confidence": 0.1}]
            }))
            MockLLM.return_value = mock_instance

            result = await link_docs(
                client, settings, "myrepo",
                use_embeddings=False, min_confidence=0.3
            )

        assert result.edges_written == 0
        assert result.sections_processed == 1  # section was processed, just no edges
