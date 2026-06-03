"""MCP read tools — all have readOnlyHint=True."""

import logging
from typing import Any, Optional

from code_kg.domain.models import (
    FindNodesInput,
    FindNodesOutput,
    FoundNode,
    GetNodeInput,
    NodeDetail,
    NeighborRef,
    ImpactAnalysisInput,
    ImpactAnalysisOutput,
    ImpactPath,
)
from code_kg.graph import queries as Q
from code_kg.graph.client import Neo4jClient
from code_kg.mcp.shaping import (
    reciprocal_rank_fusion,
    shape_neighbor,
    shape_node_detail,
    shape_node_for_list,
)

logger = logging.getLogger(__name__)


def _row_to_found_node(row: dict[str, Any]) -> Optional[FoundNode]:
    """Convert a shaped row dict to a FoundNode, returning None on failure."""
    try:
        return FoundNode(
            id=row["id"],
            type=row.get("type", "Unknown"),
            name=row.get("name", ""),
            file_path=row.get("filePath"),
            summary=row.get("summary", ""),
            tags=row.get("tags") or [],
            layer=row.get("layer"),
            score=row.get("score"),  # present in search results; None in structural queries
        )
    except Exception as e:
        logger.warning(f"Could not build FoundNode from row: {e}")
        return None


# ── find_nodes ────────────────────────────────────────────────────────────────

async def find_nodes(
    client: Neo4jClient,
    inp: FindNodesInput,
    query_embedding: Optional[list[float]] = None,
) -> FindNodesOutput:
    """Hybrid full-text + vector search with RRF merging.

    Args:
        client: Neo4j client.
        inp: FindNodesInput with query, filters, and options.
        query_embedding: Pre-computed embedding for semantic search.

    Returns:
        FindNodesOutput with ranked node list.
    """
    ft_rows: list[dict] = []
    vec_rows: list[dict] = []

    # Full-text search (always)
    try:
        scope = (inp.types or [])
        is_docs_only = scope and all(t in ("Document", "Section") for t in scope)
        is_code_only = scope and all(t not in ("Document", "Section") for t in scope)

        ft_query = Q.FULLTEXT_SEARCH_DOCS if is_docs_only else Q.FULLTEXT_SEARCH_CODE
        ft_raw = await client.execute_query(ft_query, {
            "query": inp.query,
            "repos": inp.repos,
            "types": inp.types,
            "layers": inp.layers,
            "limit": inp.limit * 3,  # over-fetch for RRF
        })
        ft_rows = [shape_node_for_list(r) for r in ft_raw]
    except Exception as e:
        logger.warning(f"Full-text search failed: {e}")

    # Vector search (when embedding available)
    if inp.use_semantic and query_embedding:
        try:
            scope = (inp.types or [])
            is_docs_only = scope and all(t in ("Document", "Section") for t in scope)
            vec_query = Q.SEMANTIC_SEARCH_DOCS if is_docs_only else Q.SEMANTIC_SEARCH_CODE
            vec_raw = await client.execute_query(vec_query, {
                "query_vec": query_embedding,
                "repos": inp.repos,
                "types": inp.types,
                "layers": inp.layers,
                "top_k": inp.limit * 3,
            })
            vec_rows = [shape_node_for_list(r) for r in vec_raw]
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")

    # Merge lists
    if ft_rows and vec_rows:
        merged = reciprocal_rank_fusion(ft_rows, vec_rows, limit=inp.limit)
    elif ft_rows:
        merged = ft_rows[: inp.limit]
    elif vec_rows:
        merged = vec_rows[: inp.limit]
    else:
        merged = []

    nodes = [n for n in (_row_to_found_node(r) for r in merged) if n]
    return FindNodesOutput(nodes=nodes, total_matched=len(merged))


# ── get_node ──────────────────────────────────────────────────────────────────

async def get_node(client: Neo4jClient, inp: GetNodeInput) -> Optional[NodeDetail]:
    """Fetch a single node with its immediate neighbours.

    Args:
        client: Neo4j client.
        inp: GetNodeInput.

    Returns:
        NodeDetail or None if not found.
    """
    rows = await client.execute_query(Q.GET_NODE_BY_ID, {"id": inp.id})
    if not rows:
        return None

    detail_row = shape_node_detail(rows[0])
    neighbors: list[NeighborRef] = []

    if inp.include_neighbors:
        try:
            nbr_rows = await client.execute_query(Q.GET_NEIGHBORS, {
                "id": inp.id,
                "edge_types": inp.neighbor_edge_types,
                "limit": inp.max_neighbors,
            })
            for r in nbr_rows:
                shaped = shape_neighbor(r)
                try:
                    neighbors.append(NeighborRef(
                        id=shaped["id"],
                        type=shaped["type"],
                        name=shaped["name"],
                        edge_type=shaped.get("edge_type", ""),
                        edge_weight=shaped.get("edge_weight") or 1.0,
                    ))
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to fetch neighbours for {inp.id}: {e}")

    return NodeDetail(
        id=detail_row["id"],
        type=detail_row["type"],
        name=detail_row["name"],
        summary=detail_row.get("summary", ""),
        tags=detail_row.get("tags") or [],
        layer=detail_row.get("layer"),
        file_path=detail_row.get("filePath"),
        line_range=detail_row.get("lineRange"),
        signature=detail_row.get("signature"),
        # code_snippet not in graph — consumer reads file via file_path + line_range
        neighbors=neighbors,
    )


# ── impact_analysis ───────────────────────────────────────────────────────────

async def impact_analysis(
    client: Neo4jClient, inp: ImpactAnalysisInput
) -> Optional[ImpactAnalysisOutput]:
    """Compute upstream callers, downstream callees, tests, and docs for a node.

    Args:
        client: Neo4j client.
        inp: ImpactAnalysisInput.

    Returns:
        ImpactAnalysisOutput or None if the node doesn't exist.
    """
    # Verify node exists
    target_rows = await client.execute_query(Q.GET_NODE_BY_ID, {"id": inp.id})
    if not target_rows:
        return None

    target_fn = _row_to_found_node(shape_node_for_list(target_rows[0]))
    if not target_fn:
        return None

    params = {"id": inp.id, "max_depth": inp.max_depth}

    async def _fetch_paths(cypher: str) -> list[ImpactPath]:
        rows = await client.execute_query(cypher, {"id": inp.id})
        paths = []
        for r in rows:
            node = _row_to_found_node(shape_node_for_list(r))
            if node:
                paths.append(ImpactPath(
                    node=node,
                    depth=r.get("depth", 1),
                    path_ids=r.get("path_ids") or [],
                ))
        return paths

    upstream = await _fetch_paths(Q.IMPACT_UPSTREAM(inp.max_depth))
    downstream = await _fetch_paths(Q.IMPACT_DOWNSTREAM(inp.max_depth))

    tests: list[FoundNode] = []
    if inp.include_tests:
        try:
            test_rows = await client.execute_query(Q.IMPACT_TESTS, {"id": inp.id})
            tests = [n for n in (_row_to_found_node(shape_node_for_list(r)) for r in test_rows) if n]
        except Exception as e:
            logger.warning(f"Test lookup failed: {e}")

    docs: list[FoundNode] = []
    if inp.include_docs:
        try:
            doc_rows = await client.execute_query(Q.IMPACT_DOCS, {"id": inp.id})
            docs = [n for n in (_row_to_found_node(shape_node_for_list(r)) for r in doc_rows) if n]
        except Exception as e:
            logger.warning(f"Doc lookup failed: {e}")

    # Layer breakdown
    all_nodes = [p.node for p in upstream + downstream]
    layer_breakdown: dict[str, int] = {}
    for node in all_nodes:
        if node.layer:
            layer_breakdown[node.layer] = layer_breakdown.get(node.layer, 0) + 1

    return ImpactAnalysisOutput(
        target=target_fn,
        upstream=upstream,
        downstream=downstream,
        tests=tests,
        docs=docs,
        layer_breakdown=layer_breakdown,
    )


# ── trace_call_chain ──────────────────────────────────────────────────────────

async def trace_call_chain(
    client: Neo4jClient,
    from_id: str,
    to_id: str,
    max_depth: int = 6,
    edge_types: Optional[list[str]] = None,
) -> Optional[dict]:
    """Find the shortest call path between two nodes.

    Args:
        client: Neo4j client.
        from_id: Source node ID.
        to_id: Target node ID.
        max_depth: Maximum path length.
        edge_types: Relationship types to traverse (default: CALLS, INSTANTIATES).

    Returns:
        Dict with ``chain`` (list of node dicts) and ``depth``, or None.
    """
    # Neo4j shortestPath raises an error when start == end; short-circuit here.
    if from_id == to_id:
        return None

    rows = await client.execute_query(Q.TRACE_CALL_CHAIN(max_depth), {
        "from_id": from_id,
        "to_id": to_id,
    })
    if not rows:
        return None

    row = rows[0]
    return {
        "chain": row.get("chain", []),
        "depth": row.get("depth", 0),
    }


# ── find_tests_for ────────────────────────────────────────────────────────────

async def find_tests_for(
    client: Neo4jClient,
    node_id: str,
    min_weight: float = 0.5,
) -> list[FoundNode]:
    """Return test nodes linked to a target node by TESTS edges.

    Args:
        client: Neo4j client.
        node_id: Target node ID.
        min_weight: Minimum edge weight to include.

    Returns:
        List of FoundNode for matching test nodes.
    """
    rows = await client.execute_query(Q.FIND_TESTS_FOR, {
        "id": node_id,
        "min_weight": min_weight,
    })
    return [n for n in (_row_to_found_node(shape_node_for_list(r)) for r in rows) if n]


# ── find_docs_for ────────────────────────────────────────────────────────────


async def find_docs_for(
    client: Neo4jClient,
    node_id: str,
    min_weight: float = 0.0,
) -> list[FoundNode]:
    """Return Section/Document nodes that MENTIONS or DOCUMENTS a code node.

    Args:
        client: Neo4j client.
        node_id: Target code node ID.
        min_weight: Minimum edge weight filter (0 = all edges).

    Returns:
        List of FoundNode representing matching documentation nodes, ranked by weight.
    """
    rows = await client.execute_query(Q.FIND_DOCS_FOR, {
        "id": node_id,
        "min_weight": min_weight if min_weight > 0 else None,
    })
    return [n for n in (_row_to_found_node(shape_node_for_list(r)) for r in rows) if n]


# ── find_code_for ─────────────────────────────────────────────────────────────


async def find_code_for(
    client: Neo4jClient,
    section_id: str,
    min_weight: float = 0.0,
) -> list[FoundNode]:
    """Return code nodes that a documentation section MENTIONS or DOCUMENTS.

    Args:
        client: Neo4j client.
        section_id: Source Section/Document node ID.
        min_weight: Minimum edge weight filter (0 = all edges).

    Returns:
        List of FoundNode representing matching code nodes, ranked by weight.
    """
    rows = await client.execute_query(Q.FIND_CODE_FOR, {
        "id": section_id,
        "min_weight": min_weight if min_weight > 0 else None,
    })
    return [n for n in (_row_to_found_node(shape_node_for_list(r)) for r in rows) if n]


# ── semantic_search ───────────────────────────────────────────────────────────

async def semantic_search(
    client: Neo4jClient,
    query_embedding: list[float],
    scope: str = "all",
    top_k: int = 10,
    repos: Optional[list[str]] = None,
) -> list[FoundNode]:
    """Pure vector search, bypassing full-text.

    Args:
        client: Neo4j client.
        query_embedding: Query vector.
        scope: ``"code"``, ``"docs"``, or ``"all"``.
        top_k: Number of results.
        repos: Optional repo filter.

    Returns:
        Ranked list of FoundNode.
    """
    results: list[dict] = []

    if scope in ("code", "all"):
        try:
            rows = await client.execute_query(Q.SEMANTIC_SEARCH_CODE, {
                "query_vec": query_embedding,
                "repos": repos,
                "types": None,
                "layers": None,
                "top_k": top_k,
            })
            results.extend(shape_node_for_list(r) for r in rows)
        except Exception as e:
            logger.warning(f"Code vector search failed: {e}")

    if scope in ("docs", "all"):
        try:
            rows = await client.execute_query(Q.SEMANTIC_SEARCH_DOCS, {
                "query_vec": query_embedding,
                "repos": repos,
                "top_k": top_k,
            })
            results.extend(shape_node_for_list(r) for r in rows)
        except Exception as e:
            logger.warning(f"Doc vector search failed: {e}")

    # Sort by score, cap at top_k
    results.sort(key=lambda r: r.get("score") or 0, reverse=True)
    return [n for n in (_row_to_found_node(r) for r in results[:top_k]) if n]
