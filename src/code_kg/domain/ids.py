"""Stable ID generation for code-kg nodes."""

import hashlib
import re
from pathlib import Path


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def sig_hash(signature: str) -> str:
    """Generate a 4-char hash of a normalized signature."""
    normalized = re.sub(r"\s+", " ", signature).strip()
    full_hash = hashlib.sha1(normalized.encode()).hexdigest()
    return full_hash[:4]


def make_repo_id(slug: str) -> str:
    """Repo ID: repo:<slug>."""
    return f"repo:{slug}"


def make_file_id(repo: str, file_path: str) -> str:
    """File ID: file:<repo>:<path>."""
    # Normalize path separators
    path_normalized = Path(file_path).as_posix()
    return f"file:{repo}:{path_normalized}"


def make_module_id(repo: str, module_name: str) -> str:
    """Module ID: module:<repo>:<name>."""
    return f"module:{repo}:{module_name}"


def make_class_id(repo: str, file_path: str, class_name: str) -> str:
    """Class ID: class:<repo>:<path>:<name>."""
    path_normalized = Path(file_path).as_posix()
    return f"class:{repo}:{path_normalized}:{class_name}"


def make_interface_id(repo: str, file_path: str, interface_name: str) -> str:
    """Interface ID: interface:<repo>:<path>:<name>."""
    path_normalized = Path(file_path).as_posix()
    return f"interface:{repo}:{path_normalized}:{interface_name}"


def make_function_id(
    repo: str,
    file_path: str,
    function_name: str,
    signature: str,
) -> str:
    """
    Function ID: function:<repo>:<path>:<name>:<sighash>.

    signature: e.g. "(string, number) => void"
    """
    path_normalized = Path(file_path).as_posix()
    hash_val = sig_hash(signature)
    return f"function:{repo}:{path_normalized}:{function_name}:{hash_val}"


def make_method_id(
    repo: str,
    file_path: str,
    class_name: str,
    method_name: str,
    signature: str,
) -> str:
    """
    Method ID: method:<repo>:<path>:<class>:<name>:<sighash>.

    signature: e.g. "(id: number) => Promise<void>"
    """
    path_normalized = Path(file_path).as_posix()
    hash_val = sig_hash(signature)
    return f"method:{repo}:{path_normalized}:{class_name}:{method_name}:{hash_val}"


def make_enum_id(repo: str, file_path: str, enum_name: str) -> str:
    """Enum ID: enum:<repo>:<path>:<name>."""
    path_normalized = Path(file_path).as_posix()
    return f"enum:{repo}:{path_normalized}:{enum_name}"


def make_endpoint_id(repo: str, verb: str, route: str) -> str:
    """Endpoint ID: endpoint:<repo>:<verb>:<route>."""
    return f"endpoint:{repo}:{verb.upper()}:{route}"


def make_document_id(repo: str, file_path: str) -> str:
    """Document ID: doc:<repo>:<path>."""
    path_normalized = Path(file_path).as_posix()
    return f"doc:{repo}:{path_normalized}"


def make_section_id(repo: str, file_path: str, heading_slug: str) -> str:
    """Section ID: doc:<repo>:<path>#<heading-slug>."""
    path_normalized = Path(file_path).as_posix()
    return f"doc:{repo}:{path_normalized}#{heading_slug}"


def make_concept_id(repo: str, concept_slug: str) -> str:
    """Concept ID: concept:<repo>:<slug>."""
    return f"concept:{repo}:{concept_slug}"


def make_layer_id(repo: str, layer_slug: str) -> str:
    """Layer ID: layer:<repo>:<slug>."""
    return f"layer:{repo}:{layer_slug}"


def extract_id_parts(node_id: str) -> dict[str, str]:
    """
    Parse a node ID back into its components.

    Examples:
    - "repo:devonhire" -> {type: "repo", slug: "devonhire"}
    - "file:devonhire:src/app.ts" -> {type: "file", repo: "devonhire", path: "src/app.ts"}
    - "method:devonhire:src/app.ts:MyClass:myMethod:a1b2" -> {...}
    """
    parts = node_id.split(":", 2)
    if len(parts) < 2:
        return {"type": "unknown"}

    result = {"type": parts[0]}

    if parts[0] == "repo":
        result["slug"] = parts[1]
    elif parts[0] == "doc" and "#" in parts[2]:
        repo, rest = parts[1], parts[2]
        path, heading_slug = rest.rsplit("#", 1)
        result["repo"] = repo
        result["path"] = path
        result["heading_slug"] = heading_slug
    elif parts[0] in ("file", "doc", "enum", "class", "interface"):
        result["repo"] = parts[1]
        result["path"] = parts[2] if len(parts) > 2 else ""
        if parts[0] != "file" and parts[0] != "doc":
            result["name"] = parts[2].split(":")[-1] if ":" in parts[2] else ""
    elif parts[0] == "endpoint":
        result["repo"] = parts[1]
        rest = parts[2] if len(parts) > 2 else ""
        if ":" in rest:
            verb, route = rest.split(":", 1)
            result["verb"] = verb
            result["route"] = route
    elif parts[0] in ("function", "method"):
        result["repo"] = parts[1]
        rest = parts[2] if len(parts) > 2 else ""
        if parts[0] == "function":
            # function:repo:path:name:hash
            pass
        else:
            # method:repo:path:class:name:hash
            pass

    return result
