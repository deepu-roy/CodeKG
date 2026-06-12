"""TypeScript/JavaScript code ingestion source."""

import asyncio
import hashlib
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import AsyncIterator

from code_kg.domain.models import RawEdge, RawNode

logger = logging.getLogger(__name__)

_SNIPPET_MAX_CHARS = 2000

# Skip noisy runtime / built-in call names that don't map to project symbols
_SKIP_CALLS = frozenset({
    "toString", "valueOf", "hasOwnProperty", "isPrototypeOf",
    "push", "pop", "shift", "unshift", "splice", "slice", "concat",
    "map", "filter", "reduce", "forEach", "find", "findIndex",
    "includes", "some", "every", "flat", "flatMap", "sort", "reverse",
    "join", "split", "trim", "replace", "match", "test",
    "log", "warn", "error", "info", "debug",
    "resolve", "reject", "then", "catch", "finally",
    "subscribe", "unsubscribe", "next", "complete",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "JSON", "Object", "Array", "String", "Number", "Boolean",
    "Promise", "Symbol", "Error", "console",
})


def _parse_file_sync(repo_root: str, file_path: str, language: str) -> dict:
    """Parse a single file synchronously. Top-level for ProcessPoolExecutor pickling."""
    from pathlib import Path
    full_path = Path(repo_root) / file_path
    try:
        code = full_path.read_bytes()
    except Exception as e:
        return {"file_path": file_path, "code": b"", "captures": {}, "error": str(e)}
    try:
        from code_kg.ingestion.tree_sitter_runtime import run_query
        captures = run_query(code, language)
    except Exception as e:
        return {"file_path": file_path, "code": b"", "captures": {}, "error": str(e)}
    return {"file_path": file_path, "code": code, "captures": captures, "error": None}


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
    """Return the name of the innermost scope containing byte_offset.

    Args:
        byte_offset: The byte position to look up.
        scope_ranges: List of (name, start_byte, end_byte) tuples.

    Returns:
        The name of the innermost (tightest) containing scope, or None.
    """
    best: tuple[str, int, int] | None = None
    for name, start, end in scope_ranges:
        if start <= byte_offset <= end:
            if best is None or (end - start) < (best[2] - best[1]):
                best = (name, start, end)
    return best[0] if best else None


class TypeScriptSource:
    """Extracts code nodes and edges from TypeScript/JavaScript files."""

    name = "typescript"
    _language = "typescript"
    supported_extensions = [".ts", ".tsx", ".js", ".jsx"]

    async def extract(
        self,
        repo_root: str,
        files: list[str],
    ) -> AsyncIterator[RawNode | RawEdge]:
        """
        Extract TypeScript AST nodes and edges.

        Args:
            repo_root: Repository root directory.
            files: List of file paths to extract from.

        Yields:
            RawNode and RawEdge objects.
        """
        if not files:
            return

        loop = asyncio.get_event_loop()
        max_workers = min(4, os.cpu_count() or 1)

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                loop.run_in_executor(executor, _parse_file_sync, repo_root, f, self._language)
                for f in files
                if any(f.endswith(ext) for ext in self.supported_extensions)
            ]
            for future in asyncio.as_completed(futures):
                result = await future
                if result.get("error"):
                    logger.warning(f"Failed to parse {result['file_path']}: {result['error']}")
                    continue

                file_path = result["file_path"]
                code = result["code"]
                captures = result["captures"]

                file_hash = hashlib.md5(code).hexdigest()

                # Yield file node
                yield RawNode(
                    type="file",
                    name=Path(file_path).name,
                    file_path=file_path,
                    repo="<repo-slug>",  # replaced during normalization
                    language="typescript",
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
                            language="typescript",
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
                            language="typescript",
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
                            language="typescript",
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
                        continue  # Module-level call (e.g. top-level bootstrap) — skip

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

                # ── Yield INHERITS edges ──────────────────────────────────────────
                # Build a map from byte range to class name for attribution
                class_ranges: list[tuple[str, int, int]] = [
                    (c["name"], c["start"], c["end"])
                    for c in captures.get("class.def", [])
                    if c.get("name")
                ]

                for capture in captures.get("inherits.def", []):
                    parent_name = capture.get("name")
                    if not parent_name:
                        continue
                    from_class = _find_containing_scope(capture["start"], class_ranges)
                    if not from_class:
                        continue
                    yield RawEdge(
                        type="INHERITS",
                        from_id="<inherits-placeholder>",
                        to_id="<inherits-placeholder>",
                        weight=1.0,
                        metadata={
                            "from_class": from_class,
                            "from_file": file_path,
                            "unresolved_to": parent_name,
                        },
                    )

                # ── Yield IMPLEMENTS edges ────────────────────────────────────────
                for capture in captures.get("implements.def", []):
                    iface_name = capture.get("name")
                    if not iface_name:
                        continue
                    from_class = _find_containing_scope(capture["start"], class_ranges)
                    if not from_class:
                        continue
                    yield RawEdge(
                        type="IMPLEMENTS",
                        from_id="<implements-placeholder>",
                        to_id="<implements-placeholder>",
                        weight=1.0,
                        metadata={
                            "from_class": from_class,
                            "from_file": file_path,
                            "unresolved_to": iface_name,
                        },
                    )
