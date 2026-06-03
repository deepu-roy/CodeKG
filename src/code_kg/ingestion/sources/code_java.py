"""Java code ingestion source."""

import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator

from code_kg.domain.models import RawEdge, RawNode
from code_kg.ingestion.tree_sitter_runtime import run_query

logger = logging.getLogger(__name__)

_SNIPPET_MAX_CHARS = 2000


def _byte_offset_to_line(code: bytes | str, byte_offset: int) -> int:
    """Convert byte offset to line number (1-based)."""
    if isinstance(code, bytes):
        code = code.decode("utf-8", errors="ignore")
    return code[:byte_offset].count("\n") + 1


def _extract_snippet(code: bytes, start: int, end: int) -> str:
    """Extract code snippet from byte range, capped at _SNIPPET_MAX_CHARS."""
    return code[start:end].decode("utf-8", errors="ignore")[:_SNIPPET_MAX_CHARS]


class JavaSource:
    """Extracts code nodes and edges from Java files."""

    name = "java"
    supported_extensions = [".java"]

    async def extract(
        self,
        repo_root: str,
        files: list[str],
    ) -> AsyncIterator[RawNode | RawEdge]:
        """
        Extract Java AST nodes and edges.

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
                captures = run_query(code, "java")
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
                language="java",
                extra={"file_hash": file_hash},
            )

            # Extract classes
            for capture in captures.get("class.def", []):
                class_name = capture.get("name")
                if class_name:
                    yield RawNode(
                        type="class",
                        name=class_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="java",
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
                        language="java",
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # Extract methods (as functions)
            for capture in captures.get("method.def", []):
                method_name = capture.get("name")
                if method_name:
                    return_type = capture.get("return_type", "void")
                    yield RawNode(
                        type="function",
                        name=method_name,
                        file_path=file_path,
                        repo="<repo-slug>",
                        language="java",
                        signature=f"{method_name}(): {return_type}",
                        line_range=(
                            _byte_offset_to_line(code, capture["start"]),
                            _byte_offset_to_line(code, capture["end"]),
                        ),
                        code_snippet=_extract_snippet(code, capture["start"], capture["end"]),
                    )

            # Extract import statements
            for capture in captures.get("import.def", []):
                import_path = capture.get("path")
                if import_path:
                    yield RawEdge(
                        type="IMPORTS",
                        from_id=f"file:<repo>:{file_path}",
                        to_id="<import-will-be-resolved>",
                        metadata={"import_path": import_path},
                    )
