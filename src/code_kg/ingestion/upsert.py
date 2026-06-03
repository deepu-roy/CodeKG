"""Upsert pipeline: normalize raw nodes/edges and write to Neo4j."""

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional


def _slugify_heading(text: str) -> str:
    """Convert heading text to a URL-safe slug (mirrors MarkdownSource helper)."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "section"


def _split_identifier(name: str) -> str:
    """Split a camelCase / PascalCase / snake_case identifier into space-separated tokens.

    Examples:
        ``ClientService``    → ``"client service clientservice"``
        ``GetCandidateById`` → ``"get candidate by id getcandidatebyid"``
        ``user_profile``     → ``"user profile user_profile"``

    The original name is also included so exact-match searches still work.
    """
    # Split camelCase/PascalCase on transitions between lower→upper, upper→lower(before upper run)
    tokens = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    tokens = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", tokens)
    # Also split on underscores / hyphens / dots
    tokens = re.sub(r"[_.\-]", " ", tokens)
    parts = [t.lower() for t in tokens.split() if t]
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    # Always include the original (lower) for exact match
    original_lower = name.lower()
    if original_lower not in seen:
        unique.append(original_lower)
    return " ".join(unique)

from code_kg.domain.ids import (
    make_class_id,
    make_document_id,
    make_file_id,
    make_function_id,
    make_interface_id,
    make_repo_id,
    make_section_id,
)
from code_kg.domain.models import RawEdge, RawNode, NormalizedNode, NormalizedEdge
from code_kg.graph.client import Neo4jClient
from code_kg.ingestion.layers import assign_layer

logger = logging.getLogger(__name__)

# Node types → Neo4j primary label
_LABEL_MAP = {
    "file": "File",
    "class": "Class",
    "interface": "Interface",
    "function": "Function",
    "method": "Method",
    "enum": "Enum",
    "document": "Document",
    "section": "Section",
}

# Labels that receive the :DocNode cross-label (for full-text index)
_DOC_LABELS = {"Document", "Section"}


def normalize_node(node: RawNode, repo_slug: Optional[str] = None) -> Optional[NormalizedNode]:
    """Convert a RawNode to a NormalizedNode with stable IDs.

    Args:
        node: The raw node to normalize.
        repo_slug: Optional repository slug (uses node.repo if not provided).

    Returns:
        A NormalizedNode, or None if the node type is unsupported.
    """
    repo_slug = repo_slug or node.repo
    node_type = node.type

    if node_type == "file":
        node_id = make_file_id(repo_slug, node.file_path)
    elif node_type == "class":
        node_id = make_class_id(repo_slug, node.file_path, node.name)
    elif node_type == "interface":
        node_id = make_interface_id(repo_slug, node.file_path, node.name)
    elif node_type in ("function", "method"):
        sig = node.signature or ""
        node_id = make_function_id(repo_slug, node.file_path, node.name, sig)
    elif node_type == "enum":
        node_id = make_class_id(repo_slug, node.file_path, node.name)
    elif node_type == "document":
        node_id = make_document_id(repo_slug, node.file_path)
    elif node_type == "section":
        heading_slug = node.extra.get("heading_slug", _slugify_heading(node.name)) if node.extra else _slugify_heading(node.name)
        node_id = make_section_id(repo_slug, node.file_path, heading_slug)
    else:
        logger.warning(f"Unknown node type: {node_type}")
        return None

    layer = assign_layer(node)

    sig_hash_val: Optional[str] = None
    if node.signature:
        sig_hash_val = hashlib.sha1(node.signature.encode()).hexdigest()[:8]

    file_hash_val: Optional[str] = node.extra.get("file_hash") if node.extra else None

    return NormalizedNode(
        id=node_id,
        type=node_type,
        name=node.name,
        repo=repo_slug,
        file_path=node.file_path,
        language=node.language,
        line_range=node.line_range,
        signature=node.signature,
        signature_hash=sig_hash_val,
        file_hash=file_hash_val,
        code_snippet=node.code_snippet,
        layer=layer,
    )


async def upsert_node(client: Neo4jClient, node: NormalizedNode) -> bool:
    """Upsert a single normalized node.

    Args:
        client: Neo4j client.
        node: The normalized node to upsert.

    Returns:
        True if successful, False otherwise.
    """
    label = _LABEL_MAP.get(node.type, "CodeNode")
    is_doc = label in _DOC_LABELS
    # CodeNode cross-label for all non-doc, non-CodeNode labels
    extra_label_clause = (
        "WITH n SET n:DocNode" if is_doc and label not in ("DocNode",)
        else ("WITH n SET n:CodeNode" if not is_doc and label != "CodeNode" else "")
    )

    query = f"""
    MERGE (n:{label} {{id: $id}})
    ON CREATE SET
        n.name          = $name,
        n.nameTokens    = $name_tokens,
        n.repo          = $repo,
        n.filePath      = $file_path,
        n.language      = $language,
        n.signature     = $signature,
        n.signatureHash = $signature_hash,
        n.fileHash      = $file_hash,
        n.lineRange     = $line_range,
        n.layer         = $layer,
        n.created_at    = datetime(),
        n.updated_at    = datetime()
    ON MATCH SET
        n.nameTokens    = $name_tokens,
        n.fileHash      = $file_hash,
        n.lineRange     = $line_range,
        n.layer         = $layer,
        n.updated_at    = datetime()
    {extra_label_clause}
    RETURN n.id AS id
    """

    # Neo4j stores lists natively; convert tuple → list so the driver can serialise it.
    line_range_val = list(node.line_range) if node.line_range else None

    params = {
        "id": node.id,
        "name": node.name,
        "name_tokens": _split_identifier(node.name),
        "repo": node.repo,
        "file_path": node.file_path or "",
        "language": node.language or "",
        "signature": node.signature or "",
        "signature_hash": node.signature_hash or "",
        "file_hash": node.file_hash or "",
        "line_range": line_range_val,
        # Store NULL (not empty string) when layer is not detected, so IS NULL / IS NOT NULL
        # filters work correctly in Cypher
        "layer": node.layer if node.layer else None,
    }

    try:
        result = await client.execute_query(query, params)
        return len(result) > 0
    except Exception as e:
        logger.error(f"Failed to upsert node {node.id}: {e}")
        return False


async def _upsert_layer_edge(client: Neo4jClient, node: NormalizedNode) -> None:
    """Create :Layer node + BELONGS_TO_LAYER relationship.

    Args:
        client: Neo4j client.
        node: Node with a layer value.
    """
    layer_id = f"layer:{node.repo}:{node.layer}"
    query = """
    MERGE (l:Layer {id: $layer_id})
    ON CREATE SET l.name = $layer_name, l.repo = $repo, l.created_at = datetime()
    WITH l
    MATCH (n {id: $node_id})
    MERGE (n)-[:BELONGS_TO_LAYER]->(l)
    """
    try:
        await client.execute_query(query, {
            "layer_id": layer_id,
            "layer_name": node.layer,
            "repo": node.repo,
            "node_id": node.id,
        })
    except Exception as e:
        logger.warning(f"Failed to create BELONGS_TO_LAYER for {node.id}: {e}")


async def _upsert_repo_edge(client: Neo4jClient, node: NormalizedNode) -> None:
    """Create :Repo node + IN_REPO relationship.

    Args:
        client: Neo4j client.
        node: Node to link to its repo.
    """
    repo_id = make_repo_id(node.repo)
    query = """
    MERGE (r:Repo {id: $repo_id})
    ON CREATE SET r.name = $repo_name, r.created_at = datetime()
    WITH r
    MATCH (n {id: $node_id})
    MERGE (n)-[:IN_REPO]->(r)
    """
    try:
        await client.execute_query(query, {
            "repo_id": repo_id,
            "repo_name": node.repo,
            "node_id": node.id,
        })
    except Exception as e:
        logger.warning(f"Failed to create IN_REPO for {node.id}: {e}")


async def upsert_edge(
    client: Neo4jClient, edge: NormalizedEdge, from_id: str, to_id: str
) -> bool:
    """Upsert a single edge.

    Both endpoint nodes must already exist (or will be created externally).

    Args:
        client: Neo4j client.
        edge: The normalized edge.
        from_id: Source node ID.
        to_id: Target node ID.

    Returns:
        True if successful, False otherwise.
    """
    try:
        rel_type = edge.type or "REFERENCES"
        # Use separate MATCH clauses (not comma-separated) to avoid Cartesian product
        # and to let Neo4j use the indexed id lookups.
        query = f"""
        MATCH (from) WHERE from.id = $from_id
        MATCH (to)   WHERE to.id   = $to_id
        MERGE (from)-[r:{rel_type}]->(to)
        ON CREATE SET
            r.weight     = $weight,
            r.unresolved = $unresolved,
            r.created_at = datetime()
        ON MATCH SET
            r.updated_at = datetime()
        RETURN r
        """
        result = await client.execute_query(query, {
            "from_id": from_id,
            "to_id": to_id,
            "weight": edge.weight,
            "unresolved": edge.unresolved,
        })
        return len(result) > 0
    except Exception as e:
        logger.error(f"Failed to upsert edge {edge.type} {from_id}->{to_id}: {e}")
        return False


async def upsert_nodes_batch(
    client: Neo4jClient, nodes: list[NormalizedNode], batch_size: int = 100
) -> int:
    """Upsert multiple nodes in batches, creating Layer and Repo edges too.

    Args:
        client: Neo4j client.
        nodes: List of normalized nodes.
        batch_size: Number of nodes per batch.

    Returns:
        Number of successfully upserted nodes.
    """
    success_count = 0

    for i in range(0, len(nodes), batch_size):
        batch = nodes[i : i + batch_size]
        logger.info(f"Upserting batch {i // batch_size + 1}: {len(batch)} nodes")

        for node in batch:
            ok = await upsert_node(client, node)
            if ok:
                success_count += 1
                if node.layer:
                    await _upsert_layer_edge(client, node)
                await _upsert_repo_edge(client, node)

    logger.info(f"Upserted {success_count}/{len(nodes)} nodes")
    return success_count


def _resolve_calls_edges(
    raw_edges: list[RawEdge],
    repo_slug: str,
    normalized_nodes: list[NormalizedNode],
) -> list[tuple[NormalizedEdge, str, str]]:
    """Resolve CALLS edges using the symbol registry built from normalized nodes.

    Each CALLS raw edge carries metadata:
        ``from_method``: name of the calling function/method
        ``from_file``:   relative file path of the caller
        ``call_name``:   name of the called function/method

    Resolution strategy:
    1. Resolve ``from_id``: exact match on (file_path, method_name).
    2. Resolve ``to_id``:
       a. Same-file match (file_path, call_name) — highest confidence.
       b. Cross-file match by name only — emit one edge per candidate,
          marked ``unresolved=True`` so the edge is queryable but flagged.
    3. Skip edges where the caller cannot be resolved (external / parse artefacts).

    Args:
        raw_edges: All raw edges from extraction.
        repo_slug: Repository slug.
        normalized_nodes: Normalized nodes with stable IDs.

    Returns:
        List of (NormalizedEdge, from_id, to_id) ready for upsert.
    """
    # Build registries for fast lookup
    # (file_path, name) → node_id   for exact-file resolution
    file_name_to_id: dict[tuple[str, str], str] = {}
    # name → [node_ids]              for cross-file fallback
    name_to_ids: dict[str, list[str]] = {}

    for node in normalized_nodes:
        if node.type in ("function", "method") and node.file_path and node.name:
            key = (node.file_path, node.name)
            file_name_to_id[key] = node.id
            name_to_ids.setdefault(node.name, []).append(node.id)

    resolved: list[tuple[NormalizedEdge, str, str]] = []
    seen: set[tuple[str, str]] = set()  # deduplicate (from_id, to_id) pairs

    for edge in raw_edges:
        if edge.type != "CALLS":
            continue

        from_file: str = edge.metadata.get("from_file", "")
        from_method: str = edge.metadata.get("from_method", "")
        call_name: str = edge.metadata.get("call_name", "")

        if not (from_file and from_method and call_name):
            continue

        # Resolve caller
        from_id = file_name_to_id.get((from_file, from_method))
        if not from_id:
            continue  # caller not in symbol registry → skip

        # Resolve callee — same file first, then cross-file
        same_file_id = file_name_to_id.get((from_file, call_name))
        if same_file_id:
            candidates = [(same_file_id, False)]   # unresolved=False (high confidence)
        else:
            cross_file = name_to_ids.get(call_name, [])
            candidates = [(nid, True) for nid in cross_file]  # unresolved=True

        for to_id, unresolved in candidates:
            pair = (from_id, to_id)
            if pair in seen or from_id == to_id:
                continue
            seen.add(pair)
            norm_edge = NormalizedEdge(
                type="CALLS",
                from_id=from_id,
                to_id=to_id,
                weight=edge.weight,
                unresolved=unresolved,
            )
            resolved.append((norm_edge, from_id, to_id))

    return resolved


def _resolve_import_edges(
    raw_edges: list[RawEdge],
    repo_slug: str,
    file_id_map: dict[str, str],
) -> list[tuple[NormalizedEdge, str, str]]:
    """Resolve IMPORTS edges to (NormalizedEdge, from_id, to_id) triples.

    Resolution strategy:
    - Relative paths (./foo, ../bar) → match against known file IDs.
    - External packages / namespaces  → Module placeholder node.

    Args:
        raw_edges: All raw edges from extraction sources.
        repo_slug: Repository slug.
        file_id_map: Maps relative file path → stable file ID.

    Returns:
        List of (edge, from_id, to_id) ready for upsert.
    """
    resolved: list[tuple[NormalizedEdge, str, str]] = []

    for edge in raw_edges:
        if edge.type != "IMPORTS":
            continue

        from_id = edge.from_id.replace("<repo>", repo_slug)
        import_path = (
            edge.metadata.get("import_path")
            or edge.metadata.get("namespace")
            or ""
        )
        if not import_path:
            continue

        to_id: Optional[str] = None
        unresolved = True

        if import_path.startswith(("./", "../")):
            # Resolve relative TS import against known files
            src_file = from_id.replace(f"file:{repo_slug}:", "", 1)
            src_dir = str(Path(src_file).parent)
            raw_target = str(Path(src_dir) / import_path)
            for ext in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
                candidate = str(Path(raw_target + ext))
                if candidate in file_id_map:
                    to_id = file_id_map[candidate]
                    unresolved = False
                    break

        if to_id is None:
            to_id = f"module:{repo_slug}:{import_path}"

        norm_edge = NormalizedEdge(
            type="IMPORTS",
            from_id=from_id,
            to_id=to_id,
            weight=1.0 if not unresolved else 0.7,
            unresolved=unresolved,
        )
        resolved.append((norm_edge, from_id, to_id))

    return resolved


async def _upsert_module_node(
    client: Neo4jClient, module_id: str, name: str, repo: str
) -> None:
    """Ensure a placeholder Module node exists for external packages.

    Args:
        client: Neo4j client.
        module_id: Stable module ID.
        name: Package/namespace name.
        repo: Repository slug.
    """
    query = """
    MERGE (m:Module {id: $id})
    ON CREATE SET m.name = $name, m.repo = $repo, m.external = true, m.created_at = datetime()
    """
    try:
        await client.execute_query(query, {"id": module_id, "name": name, "repo": repo})
    except Exception as e:
        logger.warning(f"Failed to upsert module node {module_id}: {e}")


async def normalize_and_upsert(
    client: Neo4jClient,
    raw_nodes: list[RawNode],
    raw_edges: list[RawEdge],
    repo_slug: Optional[str] = None,
    changed_files: Optional[set[str]] = None,
) -> tuple[int, int]:
    """Normalize and upsert a batch of raw nodes and edges.

    Steps:
    1. Normalize all RawNodes → stable IDs + metadata.
    2. Upsert nodes (+ BELONGS_TO_LAYER + IN_REPO structural edges).
    3. Resolve IMPORTS edges and upsert them (with Module placeholder nodes as needed).
    4. Resolve CALLS edges via symbol registry and upsert them.
    5. Upsert doc-structural edges (HAS_SECTION, LINKS_TO, MENTIONS).

    Args:
        client: Neo4j client.
        raw_nodes: List of raw nodes to process.
        raw_edges: List of raw edges to process.
        repo_slug: Optional repository slug.

    Returns:
        Tuple of (nodes_upserted, edges_upserted).
    """
    if repo_slug is None and raw_nodes:
        repo_slug = raw_nodes[0].repo
    repo_slug = repo_slug or "unknown"

    # ── Normalize nodes ──────────────────────────────────────────────────────
    normalized_nodes: list[NormalizedNode] = []
    file_id_map: dict[str, str] = {}  # file_path → stable file ID

    for raw_node in raw_nodes:
        normalized = normalize_node(raw_node, repo_slug)
        if normalized:
            normalized_nodes.append(normalized)
            if raw_node.type == "file" and raw_node.file_path:
                file_id_map[raw_node.file_path] = normalized.id

    # ── Upsert nodes (+ structural edges) ────────────────────────────────────
    nodes_upserted = await upsert_nodes_batch(client, normalized_nodes)

    # ── Resolve and upsert IMPORTS edges ─────────────────────────────────────
    edges_upserted = 0
    resolved_imports = _resolve_import_edges(raw_edges, repo_slug, file_id_map)

    for norm_edge, from_id, to_id in resolved_imports:
        if norm_edge.unresolved and to_id.startswith("module:"):
            pkg_name = to_id.split(":", 2)[-1]
            await _upsert_module_node(client, to_id, pkg_name, repo_slug)

        ok = await upsert_edge(client, norm_edge, from_id, to_id)
        if ok:
            edges_upserted += 1

    # ── Resolve doc-structural edges (HAS_SECTION, LINKS_TO, MENTIONS) ───────
    doc_edge_types = {"HAS_SECTION", "LINKS_TO", "MENTIONS"}
    for edge in raw_edges:
        if edge.type not in doc_edge_types:
            continue

        from_id = edge.from_id.replace("<repo>", repo_slug)
        to_id = edge.to_id.replace("<repo>", repo_slug)

        # Skip MENTIONS edges where to_id is an unresolved symbol placeholder
        if to_id.startswith("<symbol>:"):
            # Phase 5 will resolve these against the full symbol registry
            logger.debug(f"Deferring MENTIONS edge for {to_id}")
            continue

        norm_edge = NormalizedEdge(
            type=edge.type,
            from_id=from_id,
            to_id=to_id,
            weight=edge.weight,
            unresolved=False,
        )
        ok = await upsert_edge(client, norm_edge, from_id, to_id)
        if ok:
            edges_upserted += 1

    # ── Resolve and upsert CALLS edges ───────────────────────────────────────
    resolved_calls = resolve_calls_edges_for_files(
        raw_edges, repo_slug, normalized_nodes, changed_files
    )
    calls_written = 0
    for norm_edge, from_id, to_id in resolved_calls:
        ok = await upsert_edge(client, norm_edge, from_id, to_id)
        if ok:
            edges_upserted += 1
            calls_written += 1

    calls_total = sum(1 for e in raw_edges if e.type == "CALLS")
    logger.info(
        f"CALLS edges: {calls_written} written / {calls_total} extracted "
        f"(unresolved external calls are skipped)"
    )

    logger.info(
        f"normalize_and_upsert: {nodes_upserted} nodes, {edges_upserted} edges"
    )
    return nodes_upserted, edges_upserted


# ── S6.3 helpers — rename tracking & targeted CALLS re-resolution ─────────────


async def soft_delete_file_nodes(
    client: Neo4jClient,
    repo_slug: str,
    file_path: str,
) -> int:
    """Soft-delete all nodes belonging to a file path.

    Used when a file is renamed or deleted in a git diff, so its old nodes are
    excluded from normal queries while staying queryable for history.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.
        file_path: Relative file path whose nodes should be soft-deleted.

    Returns:
        Number of nodes soft-deleted.
    """
    from code_kg.graph import queries as Q
    try:
        rows = await client.execute_query(
            Q.SOFT_DELETE_FILE_NODES,
            {"repo": repo_slug, "file_path": file_path},
        )
        cnt = rows[0]["cnt"] if rows else 0
        if cnt:
            logger.info(f"Soft-deleted {cnt} nodes for {file_path}")
        return cnt
    except Exception as e:
        logger.warning(f"soft_delete_file_nodes failed for {file_path}: {e}")
        return 0


async def upsert_renamed_from_edge(
    client: Neo4jClient,
    repo_slug: str,
    old_path: str,
    new_path: str,
    commit_sha: str,
) -> bool:
    """Emit a RENAMED_FROM edge from the old File node to the new File node.

    Both File nodes must already exist in the graph.  If either is missing
    (e.g. the old file was never ingested), the operation returns False
    without raising.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug.
        old_path: Old relative file path.
        new_path: New relative file path.
        commit_sha: Commit SHA where the rename occurred.

    Returns:
        True if the edge was created/updated successfully.
    """
    from code_kg.graph import queries as Q
    try:
        rows = await client.execute_query(
            Q.UPSERT_RENAMED_FROM_EDGE,
            {
                "repo": repo_slug,
                "old_path": old_path,
                "new_path": new_path,
                "commit_sha": commit_sha,
            },
        )
        return bool(rows)
    except Exception as e:
        logger.warning(f"upsert_renamed_from_edge failed ({old_path}→{new_path}): {e}")
        return False


def resolve_calls_edges_for_files(
    raw_edges: list[RawEdge],
    repo_slug: str,
    normalized_nodes: list[NormalizedNode],
    changed_files: Optional[set[str]] = None,
) -> list[tuple[NormalizedEdge, str, str]]:
    """Resolve CALLS edges, optionally scoped to a set of changed files.

    For incremental ingests, only re-resolves edges whose ``from_file`` is in
    ``changed_files``.  Uses the full ``normalized_nodes`` registry so cross-file
    calls to unchanged files are still resolved correctly.  Falls back to full
    resolution when ``changed_files`` is ``None`` or empty.

    Args:
        raw_edges: Raw edges from extraction (full or partial batch).
        repo_slug: Repository slug.
        normalized_nodes: Normalized nodes — should be the *full* repo set,
            not just the changed files, so cross-file lookups work.
        changed_files: Set of relative file paths that changed.  Pass ``None``
            or ``set()`` for a full-resolution pass.

    Returns:
        List of (NormalizedEdge, from_id, to_id) ready for upsert.
    """
    if not changed_files:
        # Full resolution — pass all CALLS edges
        return _resolve_calls_edges(raw_edges, repo_slug, normalized_nodes)

    # Scoped resolution — only emit edges whose source file changed
    scoped_edges = [
        e for e in raw_edges
        if e.type == "CALLS" and e.metadata.get("from_file") in changed_files
    ]
    # Preserve non-CALLS edges so _resolve_calls_edges ignores them correctly
    other_edges = [e for e in raw_edges if e.type != "CALLS"]
    return _resolve_calls_edges(scoped_edges + other_edges, repo_slug, normalized_nodes)
