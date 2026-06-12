"""Python code ingestion source."""

import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator

from code_kg.domain.models import RawEdge, RawNode
from code_kg.ingestion.tree_sitter_runtime import run_query

logger = logging.getLogger(__name__)

_SNIPPET_MAX_CHARS = 2000

# Skip noisy built-in / stdlib call names that don't map to project symbols
_SKIP_CALLS = frozenset({
    "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
    "list", "dict", "set", "tuple", "str", "int", "float", "bool", "bytes",
    "type", "isinstance", "issubclass", "hasattr", "getattr", "setattr",
    "super", "property", "classmethod", "staticmethod",
    "open", "read", "write", "close",
    "append", "extend", "insert", "remove", "pop", "clear", "update",
    "get", "keys", "values", "items",
    "format", "join", "split", "strip", "replace", "startswith", "endswith",
    "upper", "lower", "encode", "decode",
    "logging", "logger", "debug", "info", "warning", "error", "critical",
    "raise", "assert",
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
    """Return the name of the innermost scope containing byte_offset."""
    best: tuple[str, int, int] | None = None
    for name, start, end in scope_ranges:
        if start <= byte_offset <= end:
            if best is None or (end - start) < (best[2] - best[1]):
                best = (name, start, end)
    return best[0] if best else None


class PythonSource:
    """Extracts code nodes and edges from Python files."""

    name = "python"
    supported_extensions = [".py"]

    async def extract(
        self,
        repo_root: str,
        files: list[str],
    ) -> AsyncIterator[RawNode | RawEdge]:
        """
        Extract Python AST nodes and edges.

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
                captures = run_query(code, "python")
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
                language="python",
                extra={"file_hash": file_hash},
            )

            # ── Build scope ranges for call-site attribution ──────────────────
            scope_ranges: list[tuple[str, int, int]] = []
            for capture in captures.get("function.def", []):
                name = capture.get("name")
                if name:
                    scope_ranges.append((name, capture["start"], capture["end"]))
            for capture in captures.get("method.def", []):
                name = capture.get("name")
                if name:
                    scope_ranges.append((name, capture["start"], capture["end"]))

            # ── Extract classes ───────────────────────────────────────────────
            for capture in captures.get("class.def", []):
                class_name = capture.get("name")
                if class_name:
                    yield RawNode(
                        type="class",
                        name=class_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="python",
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # ── Extract functions ─────────────────────────────────────────────
            for capture in captures.get("function.def", []):
                function_name = capture.get("name")
                if function_name:
                    sig_text = capture.get("text", "")
                    yield RawNode(
                        type="function",
                        name=function_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="python",
                        signature=sig_text,
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
                        language="python",
                        signature=sig_text,
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # ── Extract method calls with containing-scope attribution ─────────
            for capture in captures.get("call.site", []):
                call_name = capture.get("name")
                if not call_name or call_name in _SKIP_CALLS:
                    continue

                containing = _find_containing_scope(capture["start"], scope_ranges)
                if not containing:
                    continue  # Module-level call — skip

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

            # ── Extract imports ───────────────────────────────────────────────
            for capture in captures.get("import.def", []):
                import_path = capture.get("path")
                if import_path:
                    yield RawEdge(
                        type="IMPORTS",
                        from_id=f"file:<repo>:{file_path}",
                        to_id="<import-will-be-resolved>",
                        metadata={"import_path": import_path},
                    )
