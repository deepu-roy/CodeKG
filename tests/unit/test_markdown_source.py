"""Tests for MarkdownSource ingestion."""

import os
import tempfile
from pathlib import Path

import pytest

from code_kg.domain.models import RawEdge, RawNode
from code_kg.ingestion.sources.docs_markdown import MarkdownSource, _slugify_heading, _resolve_link


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

SAMPLE_MD = """\
# My Project

Welcome to the project.

## Installation

Run `npm install`.

## Architecture

Uses `UserService` and `CandidateController` for business logic.

See [other doc](docs/guide.md) for details.
"""

FIXTURE_MD = Path(__file__).parent.parent / "fixtures" / "md" / "README.md"


async def _collect(source, tmpdir, files, **kwargs):
    nodes, edges = [], []
    async for item in source.extract(tmpdir, files, **kwargs):
        if isinstance(item, RawNode):
            nodes.append(item)
        else:
            edges.append(item)
    return nodes, edges


# ──────────────────────────────────────────────────────────────────────────────
# _slugify_heading
# ──────────────────────────────────────────────────────────────────────────────

class TestSlugifyHeading:
    def test_simple(self):
        assert _slugify_heading("My Section") == "my-section"

    def test_strips_special_chars(self):
        assert _slugify_heading("C# Setup!") == "c-setup"

    def test_multiple_spaces(self):
        assert _slugify_heading("A  B") == "a-b"

    def test_empty_returns_section(self):
        assert _slugify_heading("") == "section"


# ──────────────────────────────────────────────────────────────────────────────
# _resolve_link
# ──────────────────────────────────────────────────────────────────────────────

class TestResolveLink:
    def test_relative_same_dir(self):
        result = _resolve_link("docs/README.md", "./guide.md")
        assert result == "docs/guide.md"

    def test_relative_parent_dir(self):
        result = _resolve_link("docs/sub/page.md", "../overview.md")
        assert result == "docs/overview.md"

    def test_external_url_returns_none(self):
        result = _resolve_link("README.md", "https://example.com")
        assert result is None

    def test_anchor_only_returns_none(self):
        result = _resolve_link("README.md", "#section")
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# MarkdownSource.extract
# ──────────────────────────────────────────────────────────────────────────────

class TestMarkdownSourceExtract:
    async def test_yields_document_node(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            nodes, _ = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        doc_nodes = [n for n in nodes if n.type == "document"]
        assert len(doc_nodes) == 1
        assert doc_nodes[0].name == "README"
        assert doc_nodes[0].language == "markdown"

    async def test_yields_section_nodes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            nodes, _ = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        section_nodes = [n for n in nodes if n.type == "section"]
        section_names = [n.name for n in section_nodes]
        assert "Installation" in section_names
        assert "Architecture" in section_names

    async def test_yields_has_section_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            _, edges = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        has_section = [e for e in edges if e.type == "HAS_SECTION"]
        assert len(has_section) >= 2  # at least Installation + Architecture

    async def test_yields_links_to_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            _, edges = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        links_to = [e for e in edges if e.type == "LINKS_TO"]
        assert len(links_to) == 1
        # Must point to docs/guide.md (relative to README.md at root)
        assert links_to[0].to_id.endswith("docs/guide.md")

    async def test_yields_mentions_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            _, edges = await _collect(
                MarkdownSource(),
                tmpdir,
                ["README.md"],
                symbol_names={"UserService", "CandidateController"},
            )

        mentions = [e for e in edges if e.type == "MENTIONS"]
        mentioned_symbols = {e.metadata["symbol_name"] for e in mentions}
        assert "UserService" in mentioned_symbols
        assert "CandidateController" in mentioned_symbols

    async def test_no_mentions_without_symbol_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            _, edges = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        mentions = [e for e in edges if e.type == "MENTIONS"]
        assert len(mentions) == 0

    async def test_skips_non_markdown_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "app.ts")).write_text("export class Foo {}")
            nodes, _ = await _collect(MarkdownSource(), tmpdir, ["app.ts"])

        assert len(nodes) == 0

    async def test_section_has_code_snippet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            nodes, _ = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        sections = [n for n in nodes if n.type == "section"]
        assert all(n.code_snippet is not None for n in sections)

    async def test_document_has_content_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "README.md")).write_text(SAMPLE_MD)
            nodes, _ = await _collect(MarkdownSource(), tmpdir, ["README.md"])

        doc = next(n for n in nodes if n.type == "document")
        assert "content_hash" in doc.extra
        assert len(doc.extra["content_hash"]) == 32  # MD5 hex

    async def test_fixture_readme(self):
        """Smoke-test the fixture README.md."""
        if not FIXTURE_MD.exists():
            pytest.skip("Fixture not found")

        tmpdir = str(FIXTURE_MD.parent.parent.parent)  # tests/
        file_rel = str(FIXTURE_MD.relative_to(tmpdir))
        nodes, edges = await _collect(MarkdownSource(), tmpdir, [file_rel])

        assert any(n.type == "document" for n in nodes)
        assert any(n.type == "section" for n in nodes)
