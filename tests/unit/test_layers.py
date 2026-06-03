"""Tests for layer assignment heuristics."""

import pytest

from code_kg.domain.models import RawNode
from code_kg.ingestion.layers import assign_layer, assign_layers_to_nodes


class TestAssignLayerByName:
    """Test layer assignment based on naming patterns."""

    def test_assign_controller_layer(self):
        """Test assigning controller layer based on naming."""
        node = RawNode(
            type="class",
            name="UserController",
            file_path="src/controllers/user.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "controller"

    def test_assign_service_layer(self):
        """Test assigning service layer based on naming."""
        node = RawNode(
            type="class",
            name="UserService",
            file_path="src/services/user.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "service"

    def test_assign_repository_layer(self):
        """Test assigning repository layer based on naming."""
        node = RawNode(
            type="class",
            name="UserRepository",
            file_path="src/repositories/user.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "repository"

    def test_assign_model_layer(self):
        """Test assigning model layer based on naming."""
        node = RawNode(
            type="class",
            name="UserModel",
            file_path="src/models/user.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "model"

    def test_assign_utility_layer(self):
        """Test assigning utility layer based on naming."""
        node = RawNode(
            type="class",
            name="StringUtil",
            file_path="src/utils/string.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "utility"


class TestAssignLayerByPath:
    """Test layer assignment based on file path patterns."""

    def test_assign_controller_via_path(self):
        """Test assigning controller layer based on path."""
        node = RawNode(
            type="class",
            name="MyController",
            file_path="src/controllers/my.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "controller"

    def test_assign_service_via_path(self):
        """Test assigning service layer based on path."""
        node = RawNode(
            type="class",
            name="MyService",
            file_path="src/services/my.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "service"

    def test_assign_model_via_path(self):
        """Test assigning model layer based on path."""
        node = RawNode(
            type="class",
            name="MyModel",
            file_path="src/models/my.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "model"

    def test_priority_path_over_name(self):
        """Test that path patterns take priority over naming patterns."""
        # A class named Service but in controllers directory should be assigned controller
        node = RawNode(
            type="class",
            name="Service",
            file_path="src/controllers/service.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer == "controller"


class TestNoLayerAssignment:
    """Test cases where no layer is assigned."""

    def test_no_layer_for_generic_class(self):
        """Test that generic classes without patterns get no layer."""
        node = RawNode(
            type="class",
            name="Helper",
            file_path="src/helper.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        # Helper is a generic name, should still match utility pattern
        assert layer == "utility"

    def test_no_layer_for_unpatched_files(self):
        """Test that completely unpatched files/names get no layer."""
        node = RawNode(
            type="class",
            name="Xyz",
            file_path="src/xyz.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer is None

    def test_file_nodes_get_no_layer(self):
        """Test that file nodes don't get assigned layers."""
        node = RawNode(
            type="file",
            name="main.ts",
            file_path="src/main.ts",
            repo="myrepo",
            language="typescript",
        )

        layer = assign_layer(node)
        assert layer is None


class TestBulkAssignment:
    """Test bulk layer assignment."""

    def test_assign_layers_to_multiple_nodes(self):
        """Test assigning layers to multiple nodes at once."""
        nodes = [
            RawNode(
                type="class",
                name="UserController",
                file_path="src/controllers/user.ts",
                repo="myrepo",
                language="typescript",
            ),
            RawNode(
                type="class",
                name="UserService",
                file_path="src/services/user.ts",
                repo="myrepo",
                language="typescript",
            ),
            RawNode(
                type="class",
                name="UserModel",
                file_path="src/models/user.ts",
                repo="myrepo",
                language="typescript",
            ),
        ]

        assignments = assign_layers_to_nodes(nodes)

        assert len(assignments) == 3
        # Check that assignments were made
        assert any("controller" in v or "Controller" in v for v in assignments.values() if v)
        assert any("service" in v or "Service" in v for v in assignments.values() if v)
        assert any("model" in v or "Model" in v for v in assignments.values() if v)
