"""Tests for normalization module (symbol registry and call resolution)."""

import pytest

from code_kg.domain.models import RawNode
from code_kg.ingestion.normalize import CallResolver, SymbolRegistry


class TestSymbolRegistry:
    """Test symbol registry functionality."""

    def test_register_and_resolve_class(self):
        """Test registering and resolving a class."""
        registry = SymbolRegistry()

        class_node = RawNode(
            type="class",
            name="MyClass",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        registry.register_symbol(class_node, "src/main.ts")

        # Resolve the class
        resolved = registry.resolve_symbol("MyClass")
        assert resolved is not None
        assert resolved.name == "MyClass"
        assert resolved.type == "class"

    def test_resolve_symbol_with_file_scope(self):
        """Test resolving a symbol scoped to a specific file."""
        registry = SymbolRegistry()

        # Register the same symbol in two files
        class1 = RawNode(
            type="class",
            name="MyClass",
            file_path="src/file1.ts",
            repo="myrepo",
            language="typescript",
        )
        class2 = RawNode(
            type="class",
            name="MyClass",
            file_path="src/file2.ts",
            repo="myrepo",
            language="typescript",
        )

        registry.register_symbol(class1, "src/file1.ts")
        registry.register_symbol(class2, "src/file2.ts")

        # Resolve without scope → should get first registered
        resolved = registry.resolve_symbol("MyClass")
        assert resolved is not None

        # Resolve with file scope → should get the one from file2
        resolved_scoped = registry.resolve_symbol("MyClass", "src/file2.ts")
        assert resolved_scoped is not None
        assert resolved_scoped.file_path == "src/file2.ts"

    def test_register_and_resolve_method(self):
        """Test registering and resolving methods in a class."""
        registry = SymbolRegistry()

        method_node = RawNode(
            type="function",
            name="getValue",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        registry.register_method("MyClass", method_node)

        # Resolve the method
        resolved = registry.resolve_method("MyClass", "getValue")
        assert resolved is not None
        assert resolved.name == "getValue"

    def test_resolve_nonexistent_symbol(self):
        """Test that resolving a nonexistent symbol returns None."""
        registry = SymbolRegistry()

        resolved = registry.resolve_symbol("NonExistent")
        assert resolved is None

    def test_register_and_list_imports(self):
        """Test registering imports for a file."""
        registry = SymbolRegistry()

        registry.register_import("src/main.ts", "System.Collections")
        registry.register_import("src/main.ts", "System.Linq")

        imports = registry.imports_by_file["src/main.ts"]
        assert "System.Collections" in imports
        assert "System.Linq" in imports


class TestCallResolver:
    """Test call resolution functionality."""

    def test_resolve_direct_function_call(self):
        """Test resolving a direct function call."""
        registry = SymbolRegistry()

        func_node = RawNode(
            type="function",
            name="getValue",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        registry.register_symbol(func_node, "src/main.ts")
        resolver = CallResolver(registry)

        resolved = resolver.resolve_call("getValue", "src/main.ts")
        assert resolved is not None
        assert resolved[0] == "function"
        assert "getValue" in resolved[1]  # ID should contain function name

    def test_resolve_method_call(self):
        """Test resolving a method call (e.g., obj.method())."""
        registry = SymbolRegistry()

        class_node = RawNode(
            type="class",
            name="MyService",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        method_node = RawNode(
            type="function",
            name="getValue",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        registry.register_symbol(class_node, "src/main.ts")
        registry.register_method("MyService", method_node)
        resolver = CallResolver(registry)

        resolved = resolver.resolve_call("MyService.getValue", "src/main.ts")
        assert resolved is not None
        assert resolved[0] == "function"
        assert "getValue" in resolved[1]  # ID should contain method name

    def test_resolve_nonexistent_call(self):
        """Test that resolving a nonexistent call returns None."""
        registry = SymbolRegistry()
        resolver = CallResolver(registry)

        resolved = resolver.resolve_call("nonExistent", "src/main.ts")
        assert resolved is None

    def test_ignore_non_resolvable_nodes(self):
        """Test that non-resolvable node types are ignored."""
        registry = SymbolRegistry()

        # Try to register a non-extractable node type
        file_node = RawNode(
            type="file",
            name="main.ts",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        registry.register_symbol(file_node, "src/main.ts")

        # File nodes should not be registered
        resolved = registry.resolve_symbol("main.ts")
        assert resolved is None
