"""Ingestion pipeline — orchestrates parse → normalize → enrich → upsert.

This module extracts the core pipeline logic from the CLI so it can be
called by both the CLI (``code-kg ingest``) and the MCP write tool
(``ingest_repo``).
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from code_kg.domain.models import IngestRepoOutput, RawEdge, RawNode
from code_kg.graph.client import Neo4jClient
from code_kg.ingestion.cleanup import mark_files_seen, soft_delete_orphans, update_repo_meta
from code_kg.ingestion.sources.code_csharp import CSharpSource
from code_kg.ingestion.sources.code_go import GoSource
from code_kg.ingestion.sources.code_java import JavaSource
from code_kg.ingestion.sources.code_python import PythonSource
from code_kg.ingestion.sources.code_typescript import TypeScriptSource
from code_kg.ingestion.sources.docs_markdown import MarkdownSource
from code_kg.ingestion.upsert import normalize_and_upsert

logger = logging.getLogger(__name__)


# ── Directories always excluded regardless of glob patterns ───────────────────

EXCLUDE_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", ".svn", "dist", "build", "out",
    "bin", "obj", ".next", ".nuxt", "coverage", "__pycache__",
    ".venv", "venv", ".tox", "target",
})


@dataclass
class PipelineResult:
    """Detailed outcome of a single pipeline run."""

    nodes_added: int = 0
    nodes_modified: int = 0
    nodes_removed: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def pat_to_extensions(pattern: str) -> list[str]:
    """Extract file extensions from a glob pattern like ``**/*.cs``.

    >>> pat_to_extensions("**/*.cs")
    ['.cs']
    >>> pat_to_extensions("src/**")
    []
    """
    suffix = Path(pattern).suffix
    return [suffix] if suffix else []


def collect_files(
    repo_path: Path,
    patterns: list[str],
    explicit_files: Optional[list[str]] = None,
) -> list[str]:
    """Return file paths matching *patterns* in *repo_path*, excluding junk dirs.

    Args:
        repo_path: Repository root.
        patterns: Glob patterns (e.g. ``["**/*.cs", "**/*.ts"]``).
        explicit_files: If provided, use this list directly (already resolved).

    Returns:
        Sorted, deduplicated list of relative paths.
    """
    if explicit_files is not None:
        return sorted(set(explicit_files))

    seen: set[str] = set()
    for pat in patterns:
        for f in repo_path.glob(pat):
            rel = f.relative_to(repo_path)
            if not any(part in EXCLUDE_DIRS for part in rel.parts):
                seen.add(str(rel))
    return sorted(seen)


async def extract_files(
    repo_path: Path,
    files: list[str],
) -> tuple[list[RawNode], list[RawEdge]]:
    """Run tree-sitter extraction on all *files*, routing by extension.

    Args:
        repo_path: Repository root.
        files: Relative file paths to extract.

    Returns:
        Tuple of (all_nodes, all_edges).
    """
    all_nodes: list[RawNode] = []
    all_edges: list[RawEdge] = []

    def _bucket(item: RawNode | RawEdge) -> None:
        if isinstance(item, RawNode):
            all_nodes.append(item)
        else:
            all_edges.append(item)

    ts_files = [f for f in files if f.endswith((".ts", ".tsx", ".js", ".jsx"))]
    cs_files = [f for f in files if f.endswith(".cs")]
    java_files = [f for f in files if f.endswith(".java")]
    py_files = [f for f in files if f.endswith(".py")]
    go_files = [f for f in files if f.endswith(".go")]
    md_files = [f for f in files if f.endswith((".md", ".markdown"))]

    if ts_files:
        source = TypeScriptSource()
        async for item in source.extract(str(repo_path), ts_files):
            _bucket(item)

    if cs_files:
        source = CSharpSource()
        async for item in source.extract(str(repo_path), cs_files):
            _bucket(item)

    if java_files:
        source = JavaSource()
        async for item in source.extract(str(repo_path), java_files):
            _bucket(item)

    if py_files:
        source = PythonSource()
        async for item in source.extract(str(repo_path), py_files):
            _bucket(item)

    if go_files:
        source = GoSource()
        async for item in source.extract(str(repo_path), go_files):
            _bucket(item)

    if md_files:
        symbol_names = {n.name for n in all_nodes if n.type in ("class", "function", "interface")}
        source = MarkdownSource()
        async for item in source.extract(str(repo_path), md_files, symbol_names=symbol_names):
            _bucket(item)

    return all_nodes, all_edges


async def run_pipeline(
    client: Neo4jClient,
    repo_path: Path,
    repo_slug: str,
    patterns: list[str],
    commit_sha: Optional[str] = None,
    explicit_files: Optional[list[str]] = None,
    changed_files: Optional[set[str]] = None,
    do_soft_delete: bool = True,
) -> PipelineResult:
    """Run the full parse → normalize → upsert → cleanup pipeline.

    Args:
        client: Connected Neo4j client.
        repo_path: Absolute path to the repository root.
        repo_slug: Repository slug (used as the node namespace).
        patterns: Glob patterns for file discovery.
        commit_sha: Current commit SHA (stored on nodes for soft-delete tracking).
        explicit_files: If set, skip file discovery and use this list.
        do_soft_delete: If True, soft-delete orphaned nodes after upsert.

    Returns:
        PipelineResult with counters and any non-fatal errors.
    """
    result = PipelineResult()
    t0 = time.monotonic()

    try:
        files = collect_files(repo_path, patterns, explicit_files)
        if not files:
            logger.warning(f"No files found for repo={repo_slug}")
            result.duration_seconds = time.monotonic() - t0
            return result

        logger.info(f"Extracting {len(files)} files for repo={repo_slug}")
        all_nodes, all_edges = await extract_files(repo_path, files)
        logger.info(f"Extracted {len(all_nodes)} nodes, {len(all_edges)} edges")

        nodes_upserted, edges_upserted = await normalize_and_upsert(
            client, all_nodes, all_edges, repo_slug,
            changed_files=changed_files,
        )
        result.nodes_added = nodes_upserted
        result.edges_added = edges_upserted

        # Always persist source_path so enrichment can read files without
        # the caller supplying the path again.
        await update_repo_meta(
            client, repo_slug,
            commit_sha=commit_sha or "HEAD",
            source_path=str(repo_path.resolve()),
        )
        if commit_sha:
            await mark_files_seen(client, repo_slug, files, commit_sha)
            if do_soft_delete:
                removed = await soft_delete_orphans(client, repo_slug, commit_sha)
                result.nodes_removed = removed

    except Exception as e:
        logger.error(f"Pipeline error for repo={repo_slug}: {e}", exc_info=True)
        result.errors.append(str(e))

    result.duration_seconds = round(time.monotonic() - t0, 2)
    return result


def to_ingest_output(
    job_id: str,
    result: PipelineResult,
) -> IngestRepoOutput:
    """Convert a PipelineResult to the MCP IngestRepoOutput model.

    Args:
        job_id: Unique identifier for this ingestion job.
        result: Raw pipeline result.

    Returns:
        Structured output ready for MCP serialization.
    """
    status = "failed" if result.errors else "completed"
    return IngestRepoOutput(
        job_id=job_id,
        status=status,
        nodes_added=result.nodes_added,
        nodes_modified=result.nodes_modified,
        nodes_removed=result.nodes_removed,
        edges_added=result.edges_added,
        edges_removed=result.edges_removed,
        duration_seconds=result.duration_seconds,
        errors=result.errors,
    )
