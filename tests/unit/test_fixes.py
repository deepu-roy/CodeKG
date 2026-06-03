"""Tests that verify the bug fixes and design-deviation corrections."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import tempfile
import os

import pytest

from code_kg.domain.models import RawEdge, RawNode, NormalizedNode
from code_kg.ingestion.upsert import (
    normalize_node,
    _resolve_import_edges,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fix 1 — JavaSource RawEdge uses correct fields (from_id / to_id)
# ──────────────────────────────────────────────────────────────────────────────

class TestJavaSourceEdgeFields:
    async def test_java_import_edge_has_correct_fields(self):
        """JavaSource must yield RawEdge with from_id and to_id, not source_file/target_path."""
        from code_kg.ingestion.sources.code_java import JavaSource

        java_code = b"import java.util.List;\npublic class Foo {}"
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "Foo.java")
            Path(fpath).write_bytes(java_code)

            source = JavaSource()
            edges = []
            async for item in source.extract(tmpdir, ["Foo.java"]):
                if isinstance(item, RawEdge):
                    edges.append(item)

        assert len(edges) >= 1
        import_edge = edges[0]
        # Must have from_id / to_id — not source_file / target_path
        assert hasattr(import_edge, "from_id")
        assert hasattr(import_edge, "to_id")
        assert "Foo.java" in import_edge.from_id
        assert import_edge.type == "IMPORTS"


# ──────────────────────────────────────────────────────────────────────────────
# Fix 2 — CLI uses isinstance() to bucket nodes vs edges
# ──────────────────────────────────────────────────────────────────────────────

class TestCLIBucketing:
    async def test_all_rawnode_types_go_to_nodes_bucket(self):
        """Classes, functions, interfaces extracted from TS must end up in all_nodes."""
        from code_kg.ingestion.sources.code_typescript import TypeScriptSource

        ts_code = b"""
        export class MyService {}
        export function helper(): void {}
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "app.ts")
            Path(fpath).write_bytes(ts_code)

            source = TypeScriptSource()
            nodes = []
            edges = []
            async for item in source.extract(tmpdir, ["app.ts"]):
                if isinstance(item, RawNode):
                    nodes.append(item)
                else:
                    edges.append(item)

        node_types = {n.type for n in nodes}
        # file + class + function must all land in nodes
        assert "file" in node_types
        assert "class" in node_types
        assert "function" in node_types
        # No RawNode should land in edges
        assert all(isinstance(e, RawEdge) for e in edges)


# ──────────────────────────────────────────────────────────────────────────────
# Fix 3 — code_snippet populated from byte offsets
# ──────────────────────────────────────────────────────────────────────────────

class TestCodeSnippet:
    async def test_typescript_class_has_code_snippet(self):
        from code_kg.ingestion.sources.code_typescript import TypeScriptSource

        ts_code = b"export class MyService { getValue(): string { return 'x'; } }"
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "svc.ts")).write_bytes(ts_code)
            source = TypeScriptSource()
            nodes = [
                item async for item in source.extract(tmpdir, ["svc.ts"])
                if isinstance(item, RawNode) and item.type == "class"
            ]

        assert len(nodes) >= 1
        cls_node = nodes[0]
        assert cls_node.code_snippet is not None
        assert "MyService" in cls_node.code_snippet

    async def test_csharp_method_has_code_snippet(self):
        from code_kg.ingestion.sources.code_csharp import CSharpSource

        cs_code = b"""
public class Ctrl {
    public string GetData() { return "ok"; }
}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "ctrl.cs")).write_bytes(cs_code)
            source = CSharpSource()
            nodes = [
                item async for item in source.extract(tmpdir, ["ctrl.cs"])
                if isinstance(item, RawNode) and item.type == "function"
            ]

        assert len(nodes) >= 1
        assert nodes[0].code_snippet is not None

    async def test_java_method_has_code_snippet(self):
        from code_kg.ingestion.sources.code_java import JavaSource

        java_code = b"public class Svc { public void doWork() {} }"
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "Svc.java")).write_bytes(java_code)
            source = JavaSource()
            nodes = [
                item async for item in source.extract(tmpdir, ["Svc.java"])
                if isinstance(item, RawNode) and item.type == "function"
            ]

        assert len(nodes) >= 1
        assert nodes[0].code_snippet is not None

    def test_snippet_capped_at_2000_chars(self):
        from code_kg.ingestion.sources.code_typescript import _extract_snippet
        long_code = b"x" * 5000
        snippet = _extract_snippet(long_code, 0, 5000)
        assert len(snippet) == 2000


# ──────────────────────────────────────────────────────────────────────────────
# Fix 4 — file_hash populated on file nodes
# ──────────────────────────────────────────────────────────────────────────────

class TestFileHash:
    async def test_file_node_has_file_hash_in_extra(self):
        from code_kg.ingestion.sources.code_typescript import TypeScriptSource

        ts_code = b"export const x = 1;"
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(os.path.join(tmpdir, "x.ts")).write_bytes(ts_code)
            source = TypeScriptSource()
            file_nodes = [
                item async for item in source.extract(tmpdir, ["x.ts"])
                if isinstance(item, RawNode) and item.type == "file"
            ]

        assert len(file_nodes) == 1
        assert "file_hash" in file_nodes[0].extra
        assert len(file_nodes[0].extra["file_hash"]) == 32  # MD5 hex

    def test_file_hash_propagates_to_normalized_node(self):
        raw = RawNode(
            type="file",
            name="main.ts",
            file_path="src/main.ts",
            repo="myapp",
            language="typescript",
            extra={"file_hash": "abc123def456"},
        )
        normalized = normalize_node(raw, "myapp")
        assert normalized is not None
        assert normalized.file_hash == "abc123def456"


# ──────────────────────────────────────────────────────────────────────────────
# Fix 5 — signature_hash populated for functions
# ──────────────────────────────────────────────────────────────────────────────

class TestSignatureHash:
    def test_function_node_gets_signature_hash(self):
        raw = RawNode(
            type="function",
            name="getValue",
            file_path="src/utils.ts",
            repo="app",
            language="typescript",
            signature="getValue(): string",
        )
        normalized = normalize_node(raw, "app")
        assert normalized is not None
        assert normalized.signature_hash is not None
        assert len(normalized.signature_hash) == 8  # first 8 hex chars of SHA1

    def test_different_signatures_get_different_hashes(self):
        def _hash(sig: str) -> str:
            raw = RawNode(
                type="function",
                name="f",
                file_path="src/f.ts",
                repo="r",
                language="typescript",
                signature=sig,
            )
            return normalize_node(raw).signature_hash  # type: ignore

        assert _hash("f(): string") != _hash("f(x: number): void")


# ──────────────────────────────────────────────────────────────────────────────
# Fix 6 — IMPORTS edge resolution
# ──────────────────────────────────────────────────────────────────────────────

class TestImportEdgeResolution:
    def test_relative_import_resolves_to_known_file(self):
        edges = [
            RawEdge(
                type="IMPORTS",
                from_id="file:myapp:src/app/foo.ts",
                to_id="<import-will-be-resolved>",
                metadata={"import_path": "./bar"},
            )
        ]
        file_id_map = {"src/app/bar.ts": "file:myapp:src/app/bar.ts"}
        resolved = _resolve_import_edges(edges, "myapp", file_id_map)

        assert len(resolved) == 1
        norm_edge, from_id, to_id = resolved[0]
        assert from_id == "file:myapp:src/app/foo.ts"
        assert to_id == "file:myapp:src/app/bar.ts"
        assert not norm_edge.unresolved
        assert norm_edge.weight == 1.0

    def test_external_package_becomes_module_placeholder(self):
        edges = [
            RawEdge(
                type="IMPORTS",
                from_id="file:myapp:src/app/foo.ts",
                to_id="<import-will-be-resolved>",
                metadata={"import_path": "@angular/core"},
            )
        ]
        resolved = _resolve_import_edges(edges, "myapp", {})

        assert len(resolved) == 1
        norm_edge, from_id, to_id = resolved[0]
        assert to_id == "module:myapp:@angular/core"
        assert norm_edge.unresolved is True
        assert norm_edge.weight < 1.0

    def test_csharp_namespace_import_becomes_module_placeholder(self):
        edges = [
            RawEdge(
                type="IMPORTS",
                from_id="file:app:src/Svc.cs",
                to_id="<import-will-be-resolved>",
                metadata={"namespace": "System.Collections"},
            )
        ]
        resolved = _resolve_import_edges(edges, "app", {})

        assert len(resolved) == 1
        _, _, to_id = resolved[0]
        assert to_id == "module:app:System.Collections"

    def test_repo_placeholder_is_replaced(self):
        edges = [
            RawEdge(
                type="IMPORTS",
                from_id="file:<repo>:src/foo.ts",
                to_id="<import-will-be-resolved>",
                metadata={"import_path": "@pkg/lib"},
            )
        ]
        resolved = _resolve_import_edges(edges, "actual-repo", {})

        _, from_id, _ = resolved[0]
        assert from_id == "file:actual-repo:src/foo.ts"
        assert "<repo>" not in from_id


# ──────────────────────────────────────────────────────────────────────────────
# Fix 7 — EmbeddingProvider / SummaryProvider protocol properties
# ──────────────────────────────────────────────────────────────────────────────

class TestProviderProtocolProperties:
    def test_sentence_transformer_provider_has_name(self):
        from code_kg.providers.embedding.sentence_transformers import SentenceTransformerProvider
        from code_kg.config import EmbeddingSettings

        settings = EmbeddingSettings(provider="sentence_transformers", model="BAAI/bge-small-en-v1.5")
        p = SentenceTransformerProvider(settings)
        assert p.name == "sentence_transformers"

    def test_openai_embedding_provider_has_name(self):
        from code_kg.providers.embedding.openai import OpenAIEmbeddingProvider
        from code_kg.config import EmbeddingSettings

        settings = EmbeddingSettings(provider="openai", model="text-embedding-3-small", api_key="sk-x")
        p = OpenAIEmbeddingProvider(settings)
        assert p.name == "openai"

    def test_ollama_provider_has_name_and_model_version(self):
        from code_kg.providers.summary.ollama import OllamaProvider
        from code_kg.config import SummarySettings

        settings = SummarySettings(provider="ollama", model="qwen2.5-coder:14b")
        p = OllamaProvider(settings)
        assert p.name == "ollama"
        assert p.model_version == "qwen2.5-coder:14b"

    def test_openai_summary_provider_has_name_and_model_version(self):
        from code_kg.providers.summary.openai import OpenAIProvider
        from code_kg.config import SummarySettings

        settings = SummarySettings(provider="openai", model="gpt-4o-mini", api_key="sk-x")
        p = OpenAIProvider(settings)
        assert p.name == "openai"
        assert p.model_version == "gpt-4o-mini"
