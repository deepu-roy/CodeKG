"""Response shaping: trim fields to keep MCP responses token-efficient.

Rules (from design §6.4):
1. Never return embeddings.
2. Truncate summary to 280 chars in list responses; full summary in get_node.
3. codeSnippet is not stored in the graph — consumers read code via filePath + lineRange.
4. Cap list sizes by the limit parameter; include total_matched.
5. Use stable, short field names.
"""

from typing import Any, Optional


_SUMMARY_LIST_MAX = 280
_SUMMARY_DETAIL_MAX = 2000


def shape_node_for_list(row: dict[str, Any], include_code: bool = False) -> dict[str, Any]:
    """Shape a Neo4j result row into a compact FoundNode dict.

    Args:
        row: Raw Neo4j row dict.
        include_code: If True, include codeSnippet; otherwise omit.

    Returns:
        Trimmed dict ready for MCP response.
    """
    labels: list[str] = row.get("labels") or []
    primary_type = next(
        (l for l in labels if l not in ("CodeNode", "DocNode")),
        labels[0] if labels else "Unknown",
    )

    summary = row.get("summary") or ""
    if len(summary) > _SUMMARY_LIST_MAX:
        summary = summary[:_SUMMARY_LIST_MAX] + "…"

    shaped: dict[str, Any] = {
        "id": row.get("id"),
        "type": primary_type,
        "name": row.get("name"),
        "repo": row.get("repo"),
        "filePath": row.get("filePath"),
        "summary": summary,
        "tags": row.get("tags") or [],
        "layer": row.get("layer"),
        "score": row.get("score"),
    }

    # codeSnippet no longer stored in graph — omit entirely

    # Remove None values to keep payloads clean
    return {k: v for k, v in shaped.items() if v is not None}


def shape_node_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a Neo4j result row into a full NodeDetail dict (for get_node).

    Args:
        row: Raw Neo4j row dict.

    Returns:
        Full node detail dict.
    """
    labels: list[str] = row.get("labels") or []
    primary_type = next(
        (l for l in labels if l not in ("CodeNode", "DocNode")),
        labels[0] if labels else "Unknown",
    )

    summary = row.get("summary") or ""
    if len(summary) > _SUMMARY_DETAIL_MAX:
        summary = summary[:_SUMMARY_DETAIL_MAX] + "…"

    return {
        "id": row.get("id"),
        "type": primary_type,
        "name": row.get("name"),
        "repo": row.get("repo"),
        "filePath": row.get("filePath"),
        "summary": summary,
        "tags": row.get("tags") or [],
        "layer": row.get("layer"),
        "signature": row.get("signature"),
        "lineRange": row.get("lineRange"),
        # codeSnippet not stored — consumers read code via filePath + lineRange
    }


def shape_neighbor(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a neighbor row including the edge type.

    Args:
        row: Neo4j row with neighbor fields plus edge_type and weight.

    Returns:
        Compact NeighborRef dict.
    """
    labels: list[str] = row.get("labels") or []
    primary_type = next(
        (l for l in labels if l not in ("CodeNode", "DocNode")),
        labels[0] if labels else "Unknown",
    )

    return {
        "id": row.get("id"),
        "type": primary_type,
        "name": row.get("name"),
        "edge_type": row.get("edge_type"),
        "edge_weight": row.get("weight"),
    }


def reciprocal_rank_fusion(
    list_a: list[dict],
    list_b: list[dict],
    k: int = 60,
    limit: int = 10,
) -> list[dict]:
    """Merge two ranked lists using Reciprocal Rank Fusion.

    Args:
        list_a: First ranked list (must have 'id' key).
        list_b: Second ranked list (must have 'id' key).
        k: RRF smoothing constant (typically 60).
        limit: Maximum number of results to return.

    Returns:
        Merged list sorted by combined RRF score, up to limit items.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for rank, item in enumerate(list_a, start=1):
        node_id = item.get("id")
        if node_id:
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank)
            items[node_id] = item

    for rank, item in enumerate(list_b, start=1):
        node_id = item.get("id")
        if node_id:
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank)
            if node_id not in items:
                items[node_id] = item

    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    result = []
    for node_id in sorted_ids[:limit]:
        row = dict(items[node_id])
        row["score"] = round(scores[node_id], 6)
        result.append(row)

    return result
