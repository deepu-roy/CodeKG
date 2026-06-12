"""Enrichment pipeline: generate summaries and embeddings for normalised nodes."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from code_kg.domain.models import NormalizedNode, SummaryRequest, SummaryResponse
from code_kg.graph.client import Neo4jClient
from code_kg.providers.embedding.base import EmbeddingProvider
from code_kg.providers.summary.base import SummaryProvider

logger = logging.getLogger(__name__)

# Types that carry meaningful code to summarise
_SUMMARISABLE_TYPES = {"class", "interface", "function", "method", "file", "enum"}

# Lines to read when line_range is absent (e.g. File nodes)
_FALLBACK_LINE_COUNT = 60


def _read_code_from_file(
    source_path: str,
    file_path: Optional[str],
    line_range: Optional[tuple[int, int]],
) -> Optional[str]:
    """Read the relevant lines of a source file for a node.

    Args:
        source_path: Absolute path to the repo root (stored in :Repo.source_path).
        file_path: Relative file path stored on the node (e.g. 'src/Foo.cs').
        line_range: (start, end) 1-based inclusive line numbers, or None.

    Returns:
        The extracted source text, or None if the file cannot be read.
    """
    if not source_path or not file_path:
        return None
    try:
        full_path = Path(source_path) / file_path
        if not full_path.is_file():
            return None
        lines = full_path.read_text(errors="replace").splitlines()
        if line_range:
            start = max(0, line_range[0] - 1)   # convert 1-based to 0-based
            end = min(len(lines), line_range[1])
            return "\n".join(lines[start:end])
        # No line range — return the first N lines (e.g. for File nodes)
        return "\n".join(lines[:_FALLBACK_LINE_COUNT])
    except Exception as e:
        logger.debug(f"Could not read {file_path} from {source_path}: {e}")
        return None


def _build_summary_request(
    node: NormalizedNode,
    source_path: Optional[str] = None,
) -> Optional[SummaryRequest]:
    """Build a SummaryRequest from a NormalizedNode, or None if not applicable.

    Code is read dynamically from the source file using filePath + lineRange
    stored on the node — codeSnippet is no longer persisted in the graph.

    Args:
        node: The normalized node.
        source_path: Absolute repo root path (from :Repo.source_path).

    Returns:
        SummaryRequest, or None if this node type should not be summarised.
    """
    if node.type not in _SUMMARISABLE_TYPES:
        return None

    # Prefer code_snippet if already set (populated by enrich_batch from disk).
    # Fall back to reading from disk, then to signature, then to name.
    code_or_text = (
        node.code_snippet
        or _read_code_from_file(source_path or "", node.file_path, node.line_range)
        or node.signature
        or node.name
    )
    if code_or_text == node.name:
        logger.debug(f"No code found for {node.id} — LLM will see name only")

    if node.signature and node.signature not in code_or_text:
        code_or_text = f"{node.signature}\n\n{code_or_text}"

    return SummaryRequest(
        node_id=node.id,
        node_type=node.type,
        name=node.name,
        language=node.language,
        code_or_text=code_or_text,
        context=f"File: {node.file_path}" if node.file_path else None,
    )


async def enrich_node(
    node: NormalizedNode,
    summary_provider: SummaryProvider,
    embedding_provider: EmbeddingProvider,
) -> NormalizedNode:
    """Enrich a single node with a summary and embedding vector.

    Both the summary and embedding are generated concurrently.
    If either call fails, the node is returned with whichever fields succeeded.

    Args:
        node: The normalised node to enrich.
        summary_provider: Provider for generating text summaries.
        embedding_provider: Provider for generating embedding vectors.

    Returns:
        Updated NormalizedNode with summary and/or embedding filled in.
    """
    req = _build_summary_request(node)
    if req is None:
        return node

    summary_response: Optional[SummaryResponse] = None
    embedding: Optional[list[float]] = None

    # Run summary and embedding concurrently
    async def _get_summary() -> None:
        nonlocal summary_response
        try:
            summary_response = await summary_provider.summarise(req)
        except Exception as e:
            logger.warning(f"Summary failed for {node.id}: {e}")

    async def _get_embedding() -> None:
        nonlocal embedding
        try:
            text = req.code_or_text
            embedding = await embedding_provider.embed(text)
        except Exception as e:
            logger.warning(f"Embedding failed for {node.id}: {e}")

    await asyncio.gather(_get_summary(), _get_embedding())

    # Apply results
    if summary_response is not None:
        node = node.model_copy(update={
            "summary": summary_response.summary,
            "tags": summary_response.tags,
            "complexity": summary_response.complexity,
        })
    if embedding is not None:
        node = node.model_copy(update={"summary_embedding": embedding})

    return node


async def write_enrichment(client: Neo4jClient, node: NormalizedNode) -> bool:
    """Persist enrichment fields for a single node (used for one-off updates).

    Prefer write_enrichment_batch() when flushing multiple nodes at once.

    Args:
        client: Neo4j client.
        node: Node with enrichment fields set.

    Returns:
        True if the node was found and updated, False otherwise.
    """
    written = await write_enrichment_batch(client, [node])
    return written == 1


async def write_enrichment_batch(
    client: Neo4jClient,
    nodes: list[NormalizedNode],
) -> int:
    """Persist enrichment fields for a batch of nodes in a single Neo4j transaction.

    Uses UNWIND so N nodes cost one Bolt round-trip instead of N.
    Only updates existing nodes — never creates new ones.

    Args:
        client: Neo4j client.
        nodes: Nodes with enrichment fields set.

    Returns:
        Number of nodes successfully matched and updated.
    """
    if not nodes:
        return 0

    query = """
    UNWIND $rows AS row
    MATCH (n {id: row.id})
    SET
        n.summary           = row.summary,
        n.tags              = row.tags,
        n.complexity        = row.complexity,
        n.summary_embedding = row.summary_embedding,
        n.enriched_at       = datetime()
    RETURN n.id AS id
    """
    rows = [
        {
            "id": n.id,
            "summary": n.summary or "",
            "tags": n.tags or [],
            "complexity": n.complexity or "moderate",
            "summary_embedding": n.summary_embedding or [],
        }
        for n in nodes
    ]
    try:
        result = await client.execute_query(query, {"rows": rows})
        return len(result)
    except Exception as e:
        logger.error(f"Failed to write enrichment batch ({len(nodes)} nodes): {e}")
        return 0


async def enrich_batch(
    nodes: list[NormalizedNode],
    summary_provider: SummaryProvider,
    embedding_provider: EmbeddingProvider,
    client: Neo4jClient,
    max_concurrent: int = 1,
    source_path: Optional[str] = None,
    write_batch_size: int = 20,
) -> tuple[int, int]:
    """Enrich a list of nodes and persist results to Neo4j.

    Code for each node is read from disk using the filePath + lineRange stored
    on the node and the repo's source_path (stored in :Repo.source_path at
    ingest time).  codeSnippet is no longer persisted in the graph.

    Enrichment runs with bounded concurrency (semaphore).  Completed nodes are
    flushed to Neo4j in mini-batches of ``write_batch_size`` using a single
    UNWIND query per batch — this replaces the old one-write-per-node approach
    and cuts Bolt round-trips by ~20×.

    Args:
        nodes: List of normalised nodes to enrich.
        summary_provider: Provider for generating text summaries.
        embedding_provider: Provider for generating embedding vectors.
        client: Neo4j client.
        max_concurrent: Maximum simultaneous LLM+embedding calls.
            Keep at 1 for CPU Ollama; raise to 4–8 for GPU or OpenAI.
        source_path: Absolute path to the repo root, used to read source files.
            If None, looked up from :Repo.source_path in the graph.
        write_batch_size: How many enriched nodes to flush to Neo4j at once.
            20 is a safe default; raise to 50 for fast remote Neo4j instances.

    Returns:
        Tuple of (enriched_count, written_count).
    """
    # Look up source_path from the graph if not provided
    if not source_path and nodes:
        repo = nodes[0].repo
        try:
            rows = await client.execute_query(
                "MATCH (r:Repo) WHERE toLower(r.name) = toLower($repo) "
                "RETURN r.source_path AS source_path LIMIT 1",
                {"repo": repo},
            )
            if rows and rows[0].get("source_path"):
                source_path = rows[0]["source_path"]
                logger.info(f"Using source_path={source_path!r} for repo={repo}")
            else:
                logger.warning(
                    f"No source_path stored for repo={repo}. "
                    "Re-ingest the repo to populate it, or enrichment will use "
                    "signature/name only."
                )
        except Exception as e:
            logger.warning(f"Could not look up source_path: {e}")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _enrich_one(node: NormalizedNode) -> Optional[NormalizedNode]:
        async with semaphore:
            # Inject code from disk into the node before enrichment
            node.code_snippet = _read_code_from_file(
                source_path or "", node.file_path, node.line_range
            )
            return await enrich_node(node, summary_provider, embedding_provider)

    # Run all enrichments concurrently (but bounded by semaphore)
    enriched_nodes = await asyncio.gather(*[_enrich_one(n) for n in nodes])

    # Collect successfully enriched nodes
    ready = [
        n for n in enriched_nodes
        if n is not None and (n.summary or n.summary_embedding)
    ]
    enriched_count = len(ready)

    # Flush to Neo4j in mini-batches (one UNWIND per batch = far fewer round-trips)
    written_count = 0
    for batch_start in range(0, len(ready), write_batch_size):
        mini_batch = ready[batch_start: batch_start + write_batch_size]
        written = await write_enrichment_batch(client, mini_batch)
        written_count += written
        logger.debug(
            f"Flushed batch [{batch_start}:{batch_start + len(mini_batch)}] "
            f"— {written}/{len(mini_batch)} written"
        )

    logger.info(f"Enriched {enriched_count}/{len(nodes)} nodes, wrote {written_count} to Neo4j")
    return enriched_count, written_count


async def load_unenriched_nodes(
    client: Neo4jClient,
    repo: Optional[str] = None,
    limit: int = 500,
) -> list[NormalizedNode]:
    """Load nodes from Neo4j that have not yet been enriched (no summary).

    Args:
        client: Neo4j client.
        repo: Optional repo slug to filter by.
        limit: Maximum number of nodes to return.

    Returns:
        List of NormalizedNode objects.
    """
    if repo:
        query = """
        MATCH (n)
        WHERE n.repo = $repo AND n.summary IS NULL
          AND labels(n)[0] IN ['Class', 'Function', 'Method', 'Interface', 'File', 'Enum']
        RETURN n
        LIMIT $limit
        """
        params: dict = {"repo": repo, "limit": limit}
    else:
        query = """
        MATCH (n)
        WHERE n.summary IS NULL
          AND labels(n)[0] IN ['Class', 'Function', 'Method', 'Interface', 'File', 'Enum']
        RETURN n
        LIMIT $limit
        """
        params = {"limit": limit}

    try:
        rows = await client.execute_query(query, params)
    except Exception as e:
        logger.error(f"Failed to load unenriched nodes: {e}")
        return []

    nodes = []
    for row in rows:
        props = row.get("n", {})
        if not props:
            continue
        try:
            raw_lr = props.get("lineRange")
            line_range = tuple(raw_lr) if raw_lr and len(raw_lr) == 2 else None
            node = NormalizedNode(
                id=props["id"],
                type=props.get("type", "class").lower(),
                name=props.get("name", ""),
                repo=props.get("repo", ""),
                language=props.get("language"),
                file_path=props.get("filePath"),
                line_range=line_range,
                signature=props.get("signature"),
                layer=props.get("layer"),
            )
            nodes.append(node)
        except Exception as e:
            logger.warning(f"Could not construct node from row: {e}")

    logger.info(f"Loaded {len(nodes)} unenriched nodes")
    return nodes
