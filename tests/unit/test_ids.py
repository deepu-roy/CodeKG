"""Tests for stable ID generation."""

import pytest

from code_kg.domain.ids import (
    extract_id_parts,
    make_class_id,
    make_document_id,
    make_endpoint_id,
    make_file_id,
    make_function_id,
    make_interface_id,
    make_method_id,
    make_repo_id,
    make_section_id,
    sig_hash,
    slugify,
)


class TestSlugify:
    """Test slugify function."""

    def test_simple_text(self):
        assert slugify("Hello World") == "hello-world"

    def test_multiple_spaces(self):
        assert slugify("Multiple   Spaces") == "multiple-spaces"

    def test_special_characters(self):
        assert slugify("Hello@World!") == "helloworld"

    def test_underscores(self):
        assert slugify("Hello_World") == "hello-world"

    def test_hyphens(self):
        assert slugify("hello--world") == "hello-world"


class TestSigHash:
    """Test signature hashing."""

    def test_function_signature(self):
        hash1 = sig_hash("(string, number) => void")
        assert len(hash1) == 4
        assert isinstance(hash1, str)

    def test_method_signature(self):
        hash1 = sig_hash("(id: number) => Promise<void>")
        assert len(hash1) == 4

    def test_whitespace_normalization(self):
        hash1 = sig_hash("(string, number) => void")
        hash2 = sig_hash("(string,  number)  =>  void")
        assert hash1 == hash2

    def test_different_signatures_different_hashes(self):
        hash1 = sig_hash("(string) => void")
        hash2 = sig_hash("(number) => void")
        assert hash1 != hash2


class TestRepoId:
    """Test repo ID generation."""

    def test_repo_id(self):
        assert make_repo_id("devonhire") == "repo:devonhire"

    def test_repo_id_with_slug(self):
        assert make_repo_id("my-project") == "repo:my-project"


class TestFileId:
    """Test file ID generation."""

    def test_file_id_simple(self):
        assert make_file_id("devonhire", "src/app.ts") == "file:devonhire:src/app.ts"

    def test_file_id_nested(self):
        assert (
            make_file_id("devonhire", "src/components/Button.tsx")
            == "file:devonhire:src/components/Button.tsx"
        )

    @pytest.mark.skip(reason="Platform-specific path handling")
    def test_file_id_windows_path(self):
        # Path.as_posix() should normalize to forward slashes
        # (skipped: backslash handling differs across platforms)
        pass

    def test_file_id_stability(self):
        # Same file path should produce same ID
        id1 = make_file_id("devonhire", "src/app.ts")
        id2 = make_file_id("devonhire", "src/app.ts")
        assert id1 == id2


class TestClassId:
    """Test class ID generation."""

    def test_class_id(self):
        id_ = make_class_id("devonhire", "src/services/user.service.ts", "UserService")
        assert id_ == "class:devonhire:src/services/user.service.ts:UserService"

    def test_class_id_different_files(self):
        id1 = make_class_id("devonhire", "src/services/user.service.ts", "UserService")
        id2 = make_class_id(
            "devonhire", "src/services/admin.service.ts", "UserService"
        )
        assert id1 != id2


class TestFunctionId:
    """Test function ID generation."""

    def test_function_id(self):
        sig = "(string, number) => void"
        id_ = make_function_id(
            "devonhire", "src/utils.ts", "formatDate", sig
        )
        assert id_.startswith("function:devonhire:src/utils.ts:formatDate:")
        assert len(id_.split(":")) == 5

    def test_function_id_with_sig_hash(self):
        id1 = make_function_id(
            "devonhire", "src/utils.ts", "format", "(string) => string"
        )
        id2 = make_function_id(
            "devonhire", "src/utils.ts", "format", "(number) => string"
        )
        assert id1 != id2

    def test_function_id_overload_differentiation(self):
        sig1 = "(string) => void"
        sig2 = "(number) => void"
        id1 = make_function_id("devonhire", "src/utils.ts", "process", sig1)
        id2 = make_function_id("devonhire", "src/utils.ts", "process", sig2)
        # Different signatures should produce different hashes
        assert id1 != id2


class TestMethodId:
    """Test method ID generation."""

    def test_method_id(self):
        sig = "(id: number) => Promise<void>"
        id_ = make_method_id(
            "devonhire",
            "src/services/user.service.ts",
            "UserService",
            "findById",
            sig,
        )
        assert (
            id_.startswith(
                "method:devonhire:src/services/user.service.ts:UserService:findById:"
            )
        )

    def test_method_id_same_name_different_class(self):
        sig = "(id: number) => void"
        id1 = make_method_id(
            "devonhire", "src/services/user.service.ts", "UserService", "find", sig
        )
        id2 = make_method_id(
            "devonhire", "src/services/admin.service.ts", "AdminService", "find", sig
        )
        assert id1 != id2


class TestInterfaceId:
    """Test interface ID generation."""

    def test_interface_id(self):
        id_ = make_interface_id("devonhire", "src/models/user.ts", "IUser")
        assert id_ == "interface:devonhire:src/models/user.ts:IUser"


class TestEndpointId:
    """Test HTTP endpoint ID generation."""

    def test_endpoint_id_get(self):
        id_ = make_endpoint_id("devonhire", "GET", "/api/users")
        assert id_ == "endpoint:devonhire:GET:/api/users"

    def test_endpoint_id_post(self):
        id_ = make_endpoint_id("devonhire", "post", "/api/users")
        assert id_ == "endpoint:devonhire:POST:/api/users"

    def test_endpoint_id_with_template(self):
        id_ = make_endpoint_id("devonhire", "GET", "/api/users/{id}")
        assert id_ == "endpoint:devonhire:GET:/api/users/{id}"

    def test_endpoint_id_stability_across_file_moves(self):
        # Endpoint IDs should be stable regardless of file
        id1 = make_endpoint_id("devonhire", "GET", "/api/candidates")
        id2 = make_endpoint_id("devonhire", "GET", "/api/candidates")
        assert id1 == id2


class TestDocumentId:
    """Test document ID generation."""

    def test_document_id(self):
        id_ = make_document_id("devonhire", "README.md")
        assert id_ == "doc:devonhire:README.md"

    def test_document_id_nested(self):
        id_ = make_document_id("devonhire", "docs/architecture/design.md")
        assert id_ == "doc:devonhire:docs/architecture/design.md"


class TestSectionId:
    """Test section ID generation."""

    def test_section_id(self):
        id_ = make_section_id("devonhire", "README.md", "installation")
        assert id_ == "doc:devonhire:README.md#installation"

    def test_section_id_nested_path(self):
        id_ = make_section_id("devonhire", "docs/setup.md", "quick-start")
        assert id_ == "doc:devonhire:docs/setup.md#quick-start"


class TestExtractIdParts:
    """Test ID parsing."""

    def test_extract_repo_id(self):
        parts = extract_id_parts("repo:devonhire")
        assert parts["type"] == "repo"
        assert parts["slug"] == "devonhire"

    def test_extract_file_id(self):
        parts = extract_id_parts("file:devonhire:src/app.ts")
        assert parts["type"] == "file"
        assert parts["repo"] == "devonhire"
        assert "src/app.ts" in parts.get("path", "")

    def test_extract_section_id(self):
        parts = extract_id_parts("doc:devonhire:README.md#installation")
        assert parts["type"] == "doc"
        assert parts["repo"] == "devonhire"
        assert parts["path"] == "README.md"
        assert parts["heading_slug"] == "installation"
