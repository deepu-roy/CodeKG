"""MCP write tools — ingest, refresh, reindex, delete.

All write tools are idempotent where noted (MERGE-based upserts).
They use the same parse → normalize → upsert pipeline as the CLI.
"""

import logging
import uuid
from pathlib import Path
from typing import Optional

from code_kg.config import Settings
from code_kg.domain.models import (
    DeleteRepoInput,
    IngestRepoInput,
    IngestRepoOutput,
    RefreshEnrichmentInput,
    ReindexFileInput,
    RepoStateOutput,
)
from code_kg.graph.client import Neo4jClient
from code_kg.ingestion.cleanup import get_repo_meta, hard_delete_repo
from code_kg.ingestion.pipeline import (
    EXCLUDE_DIRS,
    extract_files,
    pat_to_extensions,
    run_pipeline,
    to_ingest_output,
)

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────


def _job_id() -> str:
    return uuid.uuid4().hex[:12]


def _is_local_path(repo_url: str) -> bool:
    """Return True if repo_url looks like a local filesystem path."""
    return repo_url.startswith(("/", "./", "../", "file://")) or (
        len(repo_url) > 1 and repo_url[1] == ":"  # Windows C:\...
    )


# ── ingest_repo ───────────────────────────────────────────────────────────────


async def ingest_repo(
    client: Neo4jClient,
    settings: Settings,
    inp: IngestRepoInput,
) -> IngestRepoOutput:
    """Primary ingestion entry point — supports local paths and GitHub URLs.

    Workflow:
    1. If ``repo_url`` is a local path → ingest directly.
    2. If it is a remote URL:
       a. Clone/fetch into ``settings.workdir``.
       b. If ``base_commit_sha`` is set → only process files changed in the diff.
       c. Otherwise → full ingest.
    3. Run parse → normalize → upsert → cleanup pipeline.
    4. Update :Repo node with latest commit SHA and ingest timestamp.

    Args:
        client: Connected Neo4j client.
        settings: Application settings (workdir, patterns, etc.).
        inp: Ingest parameters.

    Returns:
        IngestRepoOutput with job stats.
    """
    job_id = _job_id()
    logger.info(
        f"[{job_id}] ingest_repo start: repo={inp.repo_slug} "
        f"commit={inp.commit_sha[:8]} force_full={inp.force_full}"
    )

    try:
        # ── Resolve repo path ─────────────────────────────────────────────────
        if _is_local_path(inp.repo_url):
            repo_path = Path(inp.repo_url.replace("file://", ""))
            if not repo_path.is_dir():
                return IngestRepoOutput(
                    job_id=job_id, status="failed",
                    nodes_added=0, nodes_modified=0, nodes_removed=0,
                    edges_added=0, edges_removed=0, duration_seconds=0.0,
                    errors=[f"Local path does not exist: {inp.repo_url}"],
                )
        else:
            from code_kg.ingestion.git import clone_or_fetch
            workdir = Path(settings.workdir)
            repo_path = clone_or_fetch(inp.repo_url, workdir)

        # ── Determine file list ───────────────────────────────────────────────
        explicit_files: Optional[list[str]] = None
        diff = None

        if not inp.force_full and inp.base_commit_sha:
            try:
                from code_kg.ingestion.git import get_diff
                diff = get_diff(repo_path, inp.base_commit_sha, inp.commit_sha)
                explicit_files = [
                    f for f in diff.changed
                    if any(f.endswith(ext) for pat in inp.patterns
                           for ext in pat_to_extensions(pat))
                    and not any(part in EXCLUDE_DIRS for part in Path(f).parts)
                ]
                logger.info(
                    f"[{job_id}] Incremental: {len(explicit_files)} changed files "
                    f"(added={len(diff.added)}, modified={len(diff.modified)}, "
                    f"deleted={len(diff.deleted)}, renamed={len(diff.renamed)})"
                )

                # ── Handle deleted files — soft-delete their nodes ────────────
                if diff.deleted:
                    from code_kg.ingestion.upsert import soft_delete_file_nodes
                    for deleted_path in diff.deleted:
                        await soft_delete_file_nodes(client, inp.repo_slug, deleted_path)

                # ── Handle renamed files — soft-delete old, emit RENAMED_FROM ─
                if diff.renamed:
                    from code_kg.ingestion.upsert import (
                        soft_delete_file_nodes,
                        upsert_renamed_from_edge,
                    )
                    for old_path, new_path in diff.renamed:
                        await soft_delete_file_nodes(client, inp.repo_slug, old_path)
                        ok = await upsert_renamed_from_edge(
                            client, inp.repo_slug, old_path, new_path, inp.commit_sha
                        )
                        if ok:
                            logger.info(f"[{job_id}] Rename edge: {old_path} → {new_path}")

                if not explicit_files and not diff.deleted and not diff.renamed:
                    logger.info(f"[{job_id}] No relevant file changes — skipping ingest")
                    return IngestRepoOutput(
                        job_id=job_id, status="completed",
                        nodes_added=0, nodes_modified=0, nodes_removed=0,
                        edges_added=0, edges_removed=0, duration_seconds=0.0,
                    )
            except Exception as e:
                logger.warning(f"[{job_id}] Diff failed, falling back to full ingest: {e}")
                explicit_files = None

        # ── Run pipeline ──────────────────────────────────────────────────────
        changed_files = set(explicit_files) if explicit_files else None
        result = await run_pipeline(
            client=client,
            repo_path=repo_path,
            repo_slug=inp.repo_slug,
            patterns=inp.patterns,
            commit_sha=inp.commit_sha,
            explicit_files=explicit_files,
            changed_files=changed_files,
            do_soft_delete=True,
        )

        output = to_ingest_output(job_id, result)
        logger.info(
            f"[{job_id}] ingest_repo done: status={output.status} "
            f"nodes={output.nodes_added} edges={output.edges_added} "
            f"removed={output.nodes_removed} duration={output.duration_seconds}s"
        )
        return output

    except Exception as e:
        logger.error(f"[{job_id}] ingest_repo failed: {e}", exc_info=True)
        return IngestRepoOutput(
            job_id=job_id, status="failed",
            nodes_added=0, nodes_modified=0, nodes_removed=0,
            edges_added=0, edges_removed=0, duration_seconds=0.0,
            errors=[str(e)],
        )


# pat_to_extensions is imported as pat_to_extensions at module level above


# ── reindex_file ──────────────────────────────────────────────────────────────


async def reindex_file(
    client: Neo4jClient,
    inp: ReindexFileInput,
) -> dict:
    """Re-extract and upsert a single file.

    Useful during development to update a file without re-running a full ingest.

    Args:
        client: Connected Neo4j client.
        inp: Reindex parameters.

    Returns:
        Dict with ``status``, ``nodes_upserted``, ``edges_upserted``.
    """
    # Locate the file — check a few candidate root layouts
    candidates = [
        Path(inp.file_path),
        Path(".") / inp.repo / inp.file_path,
        Path(".") / inp.file_path,
    ]
    repo_path: Optional[Path] = None
    rel_path = inp.file_path

    for cand in candidates:
        if cand.is_file():
            repo_path = cand.parent
            rel_path = cand.name
            break

    if repo_path is None:
        return {"status": "failed", "error": f"File not found: {inp.file_path}"}

    try:
        nodes, edges = await extract_files(repo_path, [rel_path])
        from code_kg.ingestion.upsert import normalize_and_upsert
        n, e = await normalize_and_upsert(client, nodes, edges, inp.repo)
        return {"status": "completed", "nodes_upserted": n, "edges_upserted": e}
    except Exception as ex:
        logger.error(f"reindex_file failed for {inp.file_path}: {ex}")
        return {"status": "failed", "error": str(ex)}


# ── refresh_summaries ─────────────────────────────────────────────────────────


async def refresh_summaries(
    client: Neo4jClient,
    settings: Settings,
    inp: RefreshEnrichmentInput,
) -> dict:
    """Re-run summary + embedding enrichment for nodes that need it.

    Args:
        client: Connected Neo4j client.
        settings: Application settings (provider config).
        inp: Which nodes to re-enrich and whether to skip already-enriched ones.

    Returns:
        Dict with ``enriched``, ``written``.
    """
    from code_kg.ingestion.enrichment import enrich_batch, load_unenriched_nodes
    from code_kg.providers.embedding.base import build_embedding_provider
    from code_kg.providers.summary.base import build_summary_provider

    try:
        summary_provider = build_summary_provider(settings.summary)
        embedding_provider = build_embedding_provider(settings.embedding)
    except Exception as e:
        return {"status": "failed", "error": f"Provider init failed: {e}"}

    if inp.node_ids:
        # Load specific nodes by ID — use load_unenriched_nodes with a manual query
        # to get NormalizedNode objects consistent with enrich_batch expectations
        from code_kg.domain.models import NormalizedNode
        q_rows = await client.execute_query(
            "MATCH (n:CodeNode) WHERE n.id IN $ids RETURN n",
            {"ids": inp.node_ids},
        )
        rows: list = [r["n"] if isinstance(r, dict) else r for r in q_rows]
    else:
        rows = await load_unenriched_nodes(client, repo=inp.repo, limit=inp.limit)

    if inp.only_if_missing:
        # NormalizedNode objects use attribute access; raw dicts use .get()
        def _has_summary(r) -> bool:
            return bool(r.summary if hasattr(r, "summary") else r.get("summary"))
        rows = [r for r in rows if not _has_summary(r)]

    if not rows:
        return {"status": "completed", "enriched": 0, "written": 0}

    enriched, written = await enrich_batch(
        rows,
        summary_provider=summary_provider,
        embedding_provider=embedding_provider,
        client=client,
        max_concurrent=settings.summary.max_concurrent,
    )
    return {"status": "completed", "enriched": enriched, "written": written}


# ── delete_repo ───────────────────────────────────────────────────────────────


async def delete_repo(
    client: Neo4jClient,
    inp: DeleteRepoInput,
) -> dict:
    """Hard-delete all nodes and edges for a repository.

    Requires ``confirm=True`` to prevent accidental deletions.

    Args:
        client: Connected Neo4j client.
        inp: Delete parameters.

    Returns:
        Dict with ``status``, ``nodes_deleted``.
    """
    if not inp.confirm:
        return {
            "status": "aborted",
            "reason": "confirm=False — pass confirm=true to execute the deletion",
        }

    try:
        cnt = await hard_delete_repo(client, inp.repo_slug)
        return {"status": "completed", "nodes_deleted": cnt}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ── repo_state ────────────────────────────────────────────────────────────────


async def get_repo_state(
    client: Neo4jClient,
    repo_slug: str,
) -> RepoStateOutput:
    """Return the current ingestion state of a repository.

    Used by the GitHub Actions workflow to determine the base commit
    for incremental ingestion.

    Args:
        client: Connected Neo4j client.
        repo_slug: Repository slug.

    Returns:
        RepoStateOutput with last commit SHA, node count, etc.
    """
    meta = await get_repo_meta(client, repo_slug)
    if not meta:
        return RepoStateOutput(repo_slug=repo_slug, exists=False)

    # Count nodes
    rows = await client.execute_query(
        "MATCH (n:CodeNode {repo: $repo}) RETURN count(n) AS cnt",
        {"repo": repo_slug},
    )
    cnt = rows[0]["cnt"] if rows else 0

    last_ingest = meta.get("last_ingest_at")
    return RepoStateOutput(
        repo_slug=repo_slug,
        last_commit_sha=meta.get("last_commit_sha"),
        last_ingest_at=str(last_ingest) if last_ingest else None,
        node_count=cnt,
        exists=True,
    )


# ── run_test_map ──────────────────────────────────────────────────────────────


async def run_test_map(
    client: Neo4jClient,
    repo_slug: str,
    min_weight: float = 0.5,
) -> dict:
    """Infer and emit TESTS edges for a repository.

    Runs all three strategies (CALLS graph, file-name, path mirror) and
    writes TESTS edges for matches above *min_weight*.

    Args:
        client: Connected Neo4j client.
        repo_slug: Repository slug.
        min_weight: Minimum combined confidence to emit an edge.

    Returns:
        Dict with ``status``, ``edges_written``, ``edges_skipped``.
    """
    from code_kg.ingestion.tests_mapper import map_tests
    try:
        result = await map_tests(client, repo_slug, min_weight=min_weight)
        return {
            "status": "completed" if not result.errors else "partial",
            "edges_written": result.edges_written,
            "edges_skipped": result.edges_skipped,
            "errors": result.errors,
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ── refresh_doc_links ─────────────────────────────────────────────────────────


async def refresh_doc_links(
    client: Neo4jClient,
    settings: Settings,
    repo_slug: str,
    use_embeddings: bool = True,
    batch_size: int = 20,
    min_confidence: float = 0.3,
) -> dict:
    """Infer DOCUMENTS edges from doc sections to code nodes using the LLM.

    For each :Section node, discovers candidate code nodes (name match +
    optional vector lookup) and calls the LLM to classify which are
    actually documented.  Emits DOCUMENTS edges with the confidence weight.

    Args:
        client: Connected Neo4j client.
        settings: App settings (LLM + embedding provider config).
        repo_slug: Repository slug.
        use_embeddings: Use vector neighbour lookup in addition to name matching.
        batch_size: Sections per LLM batch.
        min_confidence: Minimum LLM confidence to emit a DOCUMENTS edge.

    Returns:
        Dict with ``status``, ``sections_processed``, ``edges_written``.
    """
    from code_kg.ingestion.docs_linker import link_docs
    try:
        result = await link_docs(
            client, settings, repo_slug,
            batch_size=batch_size,
            use_embeddings=use_embeddings,
            min_confidence=min_confidence,
        )
        return {
            "status": "completed" if not result.errors else "partial",
            "sections_processed": result.sections_processed,
            "edges_written": result.edges_written,
            "edges_skipped": result.edges_skipped,
            "errors": result.errors[:10],  # cap for MCP response size
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}
