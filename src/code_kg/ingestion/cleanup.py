"""Cleanup helpers — soft-delete orphaned nodes and hard-delete repos.

Soft-delete strategy (design §5):
  After each incremental ingest, any node whose ``last_seen_commit`` is
  *older* than the current commit is considered removed.  We set
  ``deleted_at = datetime()`` rather than deleting the node so that
  historical queries still work.  A separate ``purge_deleted`` function
  performs the actual removal for periodic maintenance.
"""

import logging
from typing import Optional

from code_kg.graph.client import Neo4jClient

logger = logging.getLogger(__name__)


# ── Cypher templates ──────────────────────────────────────────────────────────

_SOFT_DELETE_NODES = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.last_seen_commit IS NOT NULL
  AND n.last_seen_commit <> $current_commit
  AND n.deleted_at IS NULL
SET n.deleted_at = datetime()
RETURN count(n) AS cnt
"""

_MARK_LAST_SEEN = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.filePath IN $file_paths
SET n.last_seen_commit = $commit_sha
"""

_HARD_DELETE_REPO = """
MATCH (n)
WHERE (n:CodeNode OR n:DocNode OR n:Module) AND n.repo = $repo
DETACH DELETE n
RETURN count(n) AS cnt
"""

_HARD_DELETE_REPO_INFRA = """
MATCH (n:Repo {id: $repo_id})
DETACH DELETE n
MATCH (l:Layer {repo: $repo})
DETACH DELETE l
"""

_PURGE_DELETED = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.deleted_at IS NOT NULL
DETACH DELETE n
RETURN count(n) AS cnt
"""

_UPDATE_REPO_META = """
MERGE (r:Repo {id: $repo_id})
ON CREATE SET
    r.name             = $repo_name,
    r.last_commit_sha  = $commit_sha,
    r.last_ingest_at   = datetime(),
    r.source_path      = $source_path,
    r.created_at       = datetime()
ON MATCH SET
    r.last_commit_sha  = $commit_sha,
    r.last_ingest_at   = datetime(),
    r.source_path      = $source_path
"""

_GET_REPO_META = """
MATCH (r:Repo {id: $repo_id})
RETURN r.last_commit_sha AS last_commit_sha,
       r.last_ingest_at  AS last_ingest_at,
       r.source_path     AS source_path,
       r.name            AS name
"""


# ── public functions ──────────────────────────────────────────────────────────


async def mark_files_seen(
    client: Neo4jClient,
    repo_slug: str,
    file_paths: list[str],
    commit_sha: str,
) -> None:
    """Set ``last_seen_commit`` on all nodes belonging to *file_paths*.

    Called after upserting a batch of files so nodes that weren't visited
    during this ingest retain their old ``last_seen_commit`` and get
    soft-deleted by :func:`soft_delete_orphans`.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.
        file_paths: Relative file paths whose nodes were just upserted.
        commit_sha: Current commit SHA.
    """
    if not file_paths:
        return
    try:
        await client.execute_query(
            _MARK_LAST_SEEN,
            {"repo": repo_slug, "file_paths": file_paths, "commit_sha": commit_sha},
        )
    except Exception as e:
        logger.warning(f"mark_files_seen failed: {e}")


async def soft_delete_orphans(
    client: Neo4jClient,
    repo_slug: str,
    current_commit: str,
) -> int:
    """Soft-delete nodes whose ``last_seen_commit`` != *current_commit*.

    Sets ``deleted_at`` on each orphaned node so it is excluded from
    normal queries (which filter ``WHERE n.deleted_at IS NULL``) but
    remains available for history.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug to scope the cleanup.
        current_commit: SHA of the just-ingested commit.

    Returns:
        Number of nodes soft-deleted.
    """
    try:
        rows = await client.execute_query(
            _SOFT_DELETE_NODES,
            {"repo": repo_slug, "current_commit": current_commit},
        )
        cnt = rows[0]["cnt"] if rows else 0
        if cnt:
            logger.info(f"Soft-deleted {cnt} orphaned nodes for repo={repo_slug}")
        return cnt
    except Exception as e:
        logger.warning(f"soft_delete_orphans failed: {e}")
        return 0


async def purge_deleted(
    client: Neo4jClient,
    repo_slug: str,
) -> int:
    """Permanently remove nodes that were previously soft-deleted.

    Intended as a periodic maintenance operation, not run on every ingest.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.

    Returns:
        Number of nodes permanently deleted.
    """
    try:
        rows = await client.execute_query(_PURGE_DELETED, {"repo": repo_slug})
        cnt = rows[0]["cnt"] if rows else 0
        logger.info(f"Purged {cnt} soft-deleted nodes for repo={repo_slug}")
        return cnt
    except Exception as e:
        logger.warning(f"purge_deleted failed: {e}")
        return 0


async def hard_delete_repo(
    client: Neo4jClient,
    repo_slug: str,
) -> int:
    """Permanently delete all nodes and relationships for *repo_slug*.

    Also removes the associated :Repo and :Layer nodes.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.

    Returns:
        Number of nodes deleted.
    """
    repo_id = f"repo:{repo_slug}"
    try:
        rows = await client.execute_query(_HARD_DELETE_REPO, {"repo": repo_slug})
        cnt = rows[0]["cnt"] if rows else 0
        await client.execute_query(
            _HARD_DELETE_REPO_INFRA, {"repo_id": repo_id, "repo": repo_slug}
        )
        logger.info(f"Hard-deleted {cnt} nodes for repo={repo_slug}")
        return cnt
    except Exception as e:
        logger.error(f"hard_delete_repo failed for repo={repo_slug}: {e}")
        raise


async def update_repo_meta(
    client: Neo4jClient,
    repo_slug: str,
    commit_sha: str,
    source_path: Optional[str] = None,
) -> None:
    """Update / create the :Repo node with latest commit and ingest timestamp.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.
        commit_sha: Current head commit SHA.
        source_path: Absolute path to the repo root used during ingest.
            Stored so that enrichment can read source files without needing
            the caller to supply the path again.
    """
    repo_id = f"repo:{repo_slug}"
    try:
        await client.execute_query(
            _UPDATE_REPO_META,
            {
                "repo_id": repo_id,
                "repo_name": repo_slug,
                "commit_sha": commit_sha,
                "source_path": source_path or "",
            },
        )
    except Exception as e:
        logger.warning(f"update_repo_meta failed: {e}")


async def get_repo_meta(
    client: Neo4jClient,
    repo_slug: str,
) -> Optional[dict]:
    """Return the last-ingested commit SHA and ingest timestamp for *repo_slug*.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.

    Returns:
        Dict with ``last_commit_sha``, ``last_ingest_at``, ``name``, or None.
    """
    repo_id = f"repo:{repo_slug}"
    try:
        rows = await client.execute_query(_GET_REPO_META, {"repo_id": repo_id})
        return rows[0] if rows else None
    except Exception as e:
        logger.warning(f"get_repo_meta failed: {e}")
        return None
