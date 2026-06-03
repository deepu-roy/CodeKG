"""Tests for code ingestion sources."""

import pytest

from code_kg.domain.models import RawEdge, RawNode
from code_kg.ingestion.sources.code_csharp import CSharpSource
from code_kg.ingestion.sources.code_typescript import TypeScriptSource


@pytest.mark.asyncio
async def test_typescript_source_extracts_classes():
    """Test that TypeScript source extracts classes."""
    source = TypeScriptSource()

    # Create a temporary TypeScript file with a class
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        ts_file = Path(tmpdir) / "test.ts"
        ts_file.write_bytes(b"""
        export class MyClass {
            constructor(private name: string) {}
            getName(): string { return this.name; }
        }
        """)

        nodes = []
        edges = []
        async for item in source.extract(tmpdir, ["test.ts"]):
            if isinstance(item, RawNode):
                nodes.append(item)
            elif isinstance(item, RawEdge):
                edges.append(item)

        # Should have file node and class node
        assert len(nodes) >= 2
        file_nodes = [n for n in nodes if n.type == "file"]
        class_nodes = [n for n in nodes if n.type == "class"]
        assert len(file_nodes) == 1
        assert len(class_nodes) >= 1
        assert class_nodes[0].name == "MyClass"


@pytest.mark.asyncio
async def test_typescript_source_ignores_non_ts_files():
    """Test that TypeScript source ignores non-TypeScript files."""
    source = TypeScriptSource()

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        txt_file = Path(tmpdir) / "test.txt"
        txt_file.write_bytes(b"Not TypeScript code")

        nodes = []
        async for item in source.extract(tmpdir, ["test.txt"]):
            if isinstance(item, RawNode):
                nodes.append(item)

        # Should have no nodes extracted from .txt file
        assert len(nodes) == 0


@pytest.mark.asyncio
async def test_csharp_source_extracts_classes():
    """Test that C# source extracts classes."""
    source = CSharpSource()

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        cs_file = Path(tmpdir) / "MyService.cs"
        cs_file.write_bytes(b"""
        public class MyService
        {
            public string GetValue()
            {
                return "test";
            }
        }
        """)

        nodes = []
        edges = []
        async for item in source.extract(tmpdir, ["MyService.cs"]):
            if isinstance(item, RawNode):
                nodes.append(item)
            elif isinstance(item, RawEdge):
                edges.append(item)

        # Should have file node and class node
        assert len(nodes) >= 2
        file_nodes = [n for n in nodes if n.type == "file"]
        class_nodes = [n for n in nodes if n.type == "class"]
        assert len(file_nodes) == 1
        assert len(class_nodes) >= 1
        assert class_nodes[0].name == "MyService"


@pytest.mark.asyncio
async def test_csharp_source_extracts_methods():
    """Test that C# source extracts methods."""
    source = CSharpSource()

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        cs_file = Path(tmpdir) / "Service.cs"
        cs_file.write_bytes(b"""
        public class Service
        {
            public string GetValue() => "test";
            public async Task DoWork() { }
        }
        """)

        nodes = []
        async for item in source.extract(tmpdir, ["Service.cs"]):
            if isinstance(item, RawNode):
                nodes.append(item)

        # Should have file, class, and method nodes
        method_nodes = [n for n in nodes if n.type == "function"]
        # At least 2 methods should be extracted
        assert len(method_nodes) >= 2


@pytest.mark.asyncio
async def test_csharp_source_ignores_non_cs_files():
    """Test that C# source ignores non-C# files."""
    source = CSharpSource()

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        txt_file = Path(tmpdir) / "test.txt"
        txt_file.write_bytes(b"Not C# code")

        nodes = []
        async for item in source.extract(tmpdir, ["test.txt"]):
            if isinstance(item, RawNode):
                nodes.append(item)

        # Should have no nodes extracted from .txt file
        assert len(nodes) == 0
