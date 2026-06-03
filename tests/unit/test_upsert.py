"""Tests for upsert pipeline."""

import pytest

from code_kg.domain.models import RawNode, NormalizedNode
from code_kg.ingestion.upsert import normalize_node


class TestNormalizeNode:
    """Test node normalization."""

    def test_normalize_file_node(self):
        """Test normalizing a file node."""
        node = RawNode(
            type="file",
            name="main.ts",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.type == "file"
        assert normalized.name == "main.ts"
        assert "file" in normalized.id.lower()

    def test_normalize_class_node(self):
        """Test normalizing a class node."""
        node = RawNode(
            type="class",
            name="MyClass",
            file_path="src/my.ts",
            repo="myrepo",
            language="typescript",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.type == "class"
        assert normalized.name == "MyClass"
        assert "class" in normalized.id.lower()
        assert "MyClass" in normalized.id

    def test_normalize_function_node(self):
        """Test normalizing a function node."""
        node = RawNode(
            type="function",
            name="getValue",
            file_path="src/utils.ts",
            repo="myrepo",
            language="typescript",
            signature="getValue(): string",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.type == "function"
        assert normalized.name == "getValue"
        assert "function" in normalized.id.lower()

    def test_normalize_interface_node(self):
        """Test normalizing an interface node."""
        node = RawNode(
            type="interface",
            name="IService",
            file_path="src/service.ts",
            repo="myrepo",
            language="typescript",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.type == "interface"
        assert normalized.name == "IService"
        assert "interface" in normalized.id.lower()

    def test_normalize_with_layer_assignment(self):
        """Test that layer is assigned during normalization."""
        node = RawNode(
            type="class",
            name="UserService",
            file_path="src/services/user.ts",
            repo="myrepo",
            language="typescript",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.layer == "service"

    def test_normalize_with_custom_repo_slug(self):
        """Test normalizing with a custom repo slug."""
        node = RawNode(
            type="class",
            name="MyClass",
            file_path="src/my.ts",
            repo="original-repo",
            language="typescript",
        )

        normalized = normalize_node(node, repo_slug="custom-repo")
        assert normalized is not None
        assert "custom-repo" in normalized.id

    def test_normalize_c_sharp_class(self):
        """Test normalizing a C# class."""
        node = RawNode(
            type="class",
            name="UserController",
            file_path="src/Controllers/UserController.cs",
            repo="myapp",
            language="csharp",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.type == "class"
        assert normalized.language == "csharp"
        assert normalized.layer == "controller"  # Based on naming pattern

    def test_all_node_types_get_normalized(self):
        """Test that all supported node types can be normalized."""
        node_types = ["file", "class", "interface", "function", "enum"]

        for node_type in node_types:
            node = RawNode(
                type=node_type,  # type: ignore
                name="TestNode",
                file_path="src/test.ts",
                repo="test",
                language="typescript",
            )

            normalized = normalize_node(node)
            assert normalized is not None, f"Failed to normalize {node_type}"
            assert normalized.type == node_type

    def test_normalized_node_has_required_fields(self):
        """Test that normalized nodes have all required fields."""
        node = RawNode(
            type="function",
            name="test",
            file_path="src/test.ts",
            repo="test",
            language="typescript",
        )

        normalized = normalize_node(node)
        assert normalized is not None
        assert normalized.id is not None
        assert normalized.type == "function"
        assert normalized.name == "test"
        assert normalized.repo == "test"
        assert normalized.file_path == "src/test.ts"
        assert normalized.language == "typescript"
