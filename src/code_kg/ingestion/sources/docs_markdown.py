"""Markdown document ingestion source.

Produces:
- One :Document node per .md file.
- One :Section node per heading (H1–H4), linked via HAS_SECTION.
- LINKS_TO edges for [text](other-doc.md) links between documents.
- MENTIONS edges from sections to code symbols whose name appears verbatim
  in the section text (populated during normalization when the symbol registry
  is available; seeded here as unresolved edges).
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from code_kg.domain.ids import make_document_id, make_section_id
from code_kg.domain.models import RawEdge, RawNode

logger = logging.getLogger(__name__)

_HEADING_LEVELS = (1, 2, 3, 4)
_SNIPPET_MAX_CHARS = 4000


def _slugify_heading(text: str) -> str:
    """Convert heading text to a URL-safe anchor slug (GitHub style)."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "section"


def _is_internal_link(href: str) -> bool:
    """Return True if href points to a local Markdown file (not an external URL)."""
    parsed = urlparse(href)
    if parsed.scheme in ("http", "https", "ftp", "mailto"):
        return False
    path = parsed.path
    return path.endswith((".md", ".markdown")) or (path and "." not in Path(path).name)


def _resolve_link(from_file: str, href: str) -> Optional[str]:
    """Resolve a relative Markdown link to a normalised repo-relative path.

    Args:
        from_file: Repo-relative path of the source document.
        href: Raw href value from the link.

    Returns:
        Normalised relative path string, or None if not resolvable.
    """
    # Strip anchors
    href = href.split("#")[0].strip()
    if not href:
        return None
    if urlparse(href).scheme:
        return None  # external URL

    base_dir = Path(from_file).parent
    try:
        # Collapse ./ and ../ components without requiring the path to exist
        resolved = str((base_dir / href).resolve().relative_to(Path(".").resolve()))
    except (ValueError, OSError):
        # Fallback for paths that can't be made relative (shouldn't happen)
        resolved = str(base_dir / href)
    return resolved


class MarkdownSource:
    """Extracts document and section nodes from Markdown files.

    Requires ``markdown-it-py``:  ``pip install markdown-it-py``
    """

    name = "markdown"
    supported_extensions = [".md", ".markdown"]

    async def extract(
        self,
        repo_root: str,
        files: list[str],
        symbol_names: Optional[set[str]] = None,
    ) -> AsyncIterator[RawNode | RawEdge]:
        """Extract document and section nodes.

        Args:
            repo_root: Repository root directory.
            files: List of file paths to extract from.
            symbol_names: Optional set of known code symbol names for MENTIONS detection.

        Yields:
            RawNode and RawEdge objects.
        """
        try:
            from markdown_it import MarkdownIt
        except ImportError as e:
            raise ImportError(
                "markdown-it-py is required for Markdown ingestion. "
                "Install with: pip install markdown-it-py"
            ) from e

        md_parser = MarkdownIt()

        for file_path in files:
            if not any(file_path.endswith(ext) for ext in self.supported_extensions):
                continue

            full_path = Path(repo_root) / file_path
            try:
                raw_text = full_path.read_text(encoding="utf-8", errors="ignore")
                raw_bytes = full_path.read_bytes()
            except Exception as e:
                logger.warning(f"Failed to read {file_path}: {e}")
                continue

            content_hash = hashlib.md5(raw_bytes).hexdigest()
            doc_id = make_document_id("<repo>", file_path)

            # ── Document node ────────────────────────────────────────────────
            yield RawNode(
                type="document",
                name=Path(file_path).stem,
                file_path=file_path,
                repo="<repo-slug>",
                language="markdown",
                code_snippet=raw_text[:_SNIPPET_MAX_CHARS],
                extra={"content_hash": content_hash},
            )

            # Parse token stream
            tokens = md_parser.parse(raw_text)

            # Collect headings and inline content
            sections: list[dict] = []  # {level, title, slug, content_start, content}
            current_section: Optional[dict] = None
            section_text_parts: list[str] = []

            i = 0
            while i < len(tokens):
                token = tokens[i]

                if token.type == "heading_open":
                    level = int(token.tag[1])  # h1→1, h2→2, …
                    if level in _HEADING_LEVELS:
                        # Save previous section
                        if current_section is not None:
                            current_section["content"] = " ".join(section_text_parts).strip()
                            sections.append(current_section)

                        # Start new section
                        inline_token = tokens[i + 1] if i + 1 < len(tokens) else None
                        title = inline_token.content if inline_token else ""
                        slug = _slugify_heading(title)
                        current_section = {
                            "level": level,
                            "title": title,
                            "slug": slug,
                            "id": make_section_id("<repo>", file_path, slug),
                        }
                        section_text_parts = []

                elif token.type == "inline" and current_section is not None:
                    section_text_parts.append(token.content)

                i += 1

            # Flush last section
            if current_section is not None:
                current_section["content"] = " ".join(section_text_parts).strip()
                sections.append(current_section)

            # ── Section nodes + HAS_SECTION edges ───────────────────────────
            for sec in sections:
                yield RawNode(
                    type="section",
                    name=sec["title"],
                    file_path=file_path,
                    repo="<repo-slug>",
                    language="markdown",
                    code_snippet=sec["content"][:_SNIPPET_MAX_CHARS],
                    extra={
                        "heading_level": sec["level"],
                        "heading_slug": sec["slug"],
                        "content_hash": hashlib.md5(sec["content"].encode()).hexdigest(),
                    },
                )
                # HAS_SECTION: Document → Section
                yield RawEdge(
                    type="HAS_SECTION",
                    from_id=f"doc:<repo>:{file_path}",
                    to_id=f"doc:<repo>:{file_path}#{sec['slug']}",
                    weight=1.0,
                    metadata={},
                )

                # MENTIONS: Section → code symbols (whole-word matches)
                if symbol_names:
                    content = sec["content"]
                    for sym in symbol_names:
                        pattern = rf"\b{re.escape(sym)}\b"
                        if re.search(pattern, content):
                            yield RawEdge(
                                type="MENTIONS",
                                from_id=f"doc:<repo>:{file_path}#{sec['slug']}",
                                to_id=f"<symbol>:{sym}",
                                weight=0.8,
                                metadata={"symbol_name": sym, "section_slug": sec["slug"]},
                            )

            # ── LINKS_TO edges (cross-document links) ────────────────────────
            # Re-scan the raw text for Markdown link syntax [text](href)
            for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", raw_text):
                href = match.group(2).strip()
                if _is_internal_link(href):
                    target_path = _resolve_link(file_path, href)
                    if target_path:
                        yield RawEdge(
                            type="LINKS_TO",
                            from_id=f"doc:<repo>:{file_path}",
                            to_id=f"doc:<repo>:{target_path}",
                            weight=1.0,
                            metadata={"link_text": match.group(1)},
                        )
