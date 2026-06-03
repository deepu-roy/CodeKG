"""C# code ingestion source."""

import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator

from code_kg.domain.models import RawEdge, RawNode
from code_kg.ingestion.tree_sitter_runtime import run_query

logger = logging.getLogger(__name__)

_SNIPPET_MAX_CHARS = 2000

# Skip noisy system/framework call names that don't map to project symbols
_SKIP_CALLS = frozenset({
    "ToString", "GetType", "Equals", "GetHashCode", "Dispose",
    "Add", "Remove", "Clear", "Count", "Any", "Where", "Select",
    "ToList", "ToArray", "FirstOrDefault", "First", "Single",
    "SingleOrDefault", "OrderBy", "OrderByDescending", "GroupBy",
    "Contains", "TryGetValue", "TryAdd", "GetValueOrDefault",
    "nameof", "typeof", "sizeof",
})


def _byte_offset_to_line(code: bytes | str, byte_offset: int) -> int:
    """Convert byte offset to line number (1-based)."""
    if isinstance(code, bytes):
        code = code.decode("utf-8", errors="ignore")
    return code[:byte_offset].count("\n") + 1


def _extract_snippet(code: bytes, start: int, end: int) -> str:
    """Extract code snippet from byte range, capped at _SNIPPET_MAX_CHARS."""
    return code[start:end].decode("utf-8", errors="ignore")[:_SNIPPET_MAX_CHARS]


def _find_containing_scope(
    byte_offset: int,
    scope_ranges: list[tuple[str, int, int]],
) -> str | None:
    """Return the name of the innermost scope (method/function) containing byte_offset.

    Args:
        byte_offset: The byte position to look up.
        scope_ranges: List of (name, start_byte, end_byte) tuples.

    Returns:
        The name of the innermost containing scope, or None.
    """
    best: tuple[str, int, int] | None = None
    for name, start, end in scope_ranges:
        if start <= byte_offset <= end:
            # Prefer the tightest (smallest) enclosing range
            if best is None or (end - start) < (best[2] - best[1]):
                best = (name, start, end)
    return best[0] if best else None


class CSharpSource:
    """Extracts code nodes and edges from C# files."""

    name = "csharp"
    supported_extensions = [".cs"]

    async def extract(
        self,
        repo_root: str,
        files: list[str],
    ) -> AsyncIterator[RawNode | RawEdge]:
        """
        Extract C# AST nodes and edges.

        Args:
            repo_root: Repository root directory.
            files: List of file paths to extract from.

        Yields:
            RawNode and RawEdge objects.
        """
        for file_path in files:
            if not any(file_path.endswith(ext) for ext in self.supported_extensions):
                continue

            full_path = Path(repo_root) / file_path
            try:
                code = full_path.read_bytes()
            except Exception as e:
                logger.warning(f"Failed to read {file_path}: {e}")
                continue

            try:
                captures = run_query(code, "csharp")
            except Exception as e:
                logger.warning(f"Failed to parse {file_path}: {e}")
                continue

            file_hash = hashlib.md5(code).hexdigest()

            # Yield file node
            yield RawNode(
                type="file",
                name=Path(file_path).name,
                file_path=file_path,
                repo="<repo-slug>",  # replaced during normalization
                language="csharp",
                extra={"file_hash": file_hash},
            )

            # ── Build scope ranges for call-site attribution ──────────────────
            # Collect all method/constructor/property scopes so call sites can be
            # attributed to their containing function.
            scope_ranges: list[tuple[str, int, int]] = []
            for capture in captures.get("method.def", []):
                name = capture.get("name")
                if name:
                    scope_ranges.append((name, capture["start"], capture["end"]))
            for capture in captures.get("constructor.def", []):
                name = capture.get("name")
                if name:
                    scope_ranges.append((f"_ctor_{name}", capture["start"], capture["end"]))

            # ── Extract classes ───────────────────────────────────────────────
            for capture in captures.get("class.def", []):
                class_name = capture.get("name")
                if class_name:
                    yield RawNode(
                        type="class",
                        name=class_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="csharp",
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # Extract structs (treated as classes)
            for capture in captures.get("struct.def", []):
                struct_name = capture.get("name")
                if struct_name:
                    yield RawNode(
                        type="class",
                        name=struct_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="csharp",
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # Extract interfaces
            for capture in captures.get("interface.def", []):
                interface_name = capture.get("name")
                if interface_name:
                    yield RawNode(
                        type="interface",
                        name=interface_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="csharp",
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # ── Extract methods ───────────────────────────────────────────────
            for capture in captures.get("method.def", []):
                method_name = capture.get("name")
                if method_name:
                    sig_text = capture.get("text", "")
                    yield RawNode(
                        type="function",
                        name=method_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="csharp",
                        signature=sig_text,
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # ── Extract method calls with containing-method attribution ────────
            for capture in captures.get("call.site", []):
                call_name = capture.get("name")
                if not call_name or call_name in _SKIP_CALLS:
                    continue

                containing = _find_containing_scope(capture["start"], scope_ranges)
                if not containing:
                    continue  # Top-level / attribute initialiser — skip

                yield RawEdge(
                    type="CALLS",
                    from_id="<calls-placeholder>",
                    to_id="<calls-placeholder>",
                    weight=0.8,
                    metadata={
                        "call_name": call_name,
                        "from_method": containing,
                        "from_file": file_path,
                    },
                )

            # ── Extract using statements (imports) ────────────────────────────
            for capture in captures.get("using.def", []):
                namespace = capture.get("namespace")
                if not namespace:
                    # Older .scm variant uses "using.path" or "using.name"
                    namespace = capture.get("path") or capture.get("name")
                if namespace:
                    yield RawEdge(
                        type="IMPORTS",
                        from_id=f"file:<repo>:{file_path}",
                        to_id="<import-will-be-resolved>",
                        metadata={"namespace": namespace},
                    )
