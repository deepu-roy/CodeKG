"""Docs linker — infers DOCUMENTS edges from doc sections to code nodes.

For each :Section node in a repository the linker:
1. Finds candidate code nodes via exact symbol-name matching in section text.
2. Optionally supplements candidates with vector-neighbour lookup (when
   ``summary_embedding`` is populated on the section).
3. Asks the LLM to classify which candidates are actually documented in
   the section, returning a confidence score per symbol.
4. Emits ``DOCUMENTS`` edges for candidates above the confidence threshold.

This is a best-effort, non-blocking enrichment pass.  Individual section
failures are logged but never raise, so the full batch always completes.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from code_kg.config import Settings
from code_kg.graph import queries as Q
from code_kg.graph.client import Neo4jClient
from code_kg.providers.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Minimum LLM confidence to emit a DOCUMENTS edge
_MIN_CONFIDENCE = 0.3


@dataclass
class DocsLinkerResult:
    sections_processed: int = 0
    edges_written: int = 0
    edges_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"DocsLinkerResult(sections={self.sections_processed}, "
            f"written={self.edges_written}, skipped={self.edges_skipped}, "
            f"errors={len(self.errors)})"
        )


# ── candidate discovery ───────────────────────────────────────────────────────


def find_candidates_by_name(
    section_text: str,
    code_nodes: list[dict],
) -> list[dict]:
    """Return code nodes whose name appears as a whole word in *section_text*.

    Case-sensitive, whole-word matching so "Candidate" does not match
    "CandidateService".

    Args:
        section_text: The doc section's text content.
        code_nodes: List of ``{id, name, filePath}`` dicts.

    Returns:
        Subset of *code_nodes* whose name appears verbatim in the text.
    """
    matches = []
    for node in code_nodes:
        name = node.get("name", "")
        if not name or len(name) < 3:
            continue
        pattern = r"\b" + re.escape(name) + r"\b"
        if re.search(pattern, section_text):
            matches.append(node)
    return matches


async def find_candidates_by_vector(
    client: Neo4jClient,
    section_embedding: Optional[list[float]],
    repo_slug: str,
    top_k: int = 10,
) -> list[dict]:
    """Return code nodes closest to a section's embedding vector.

    Falls back gracefully to an empty list when:
    - ``section_embedding`` is None (enrichment not yet run).
    - The vector index returns no results.

    Args:
        client: Neo4j client.
        section_embedding: Section's ``summary_embedding`` vector, or None.
        repo_slug: Repo slug for filtering.
        top_k: Number of neighbours to fetch.

    Returns:
        List of ``{id, name, filePath}`` dicts.
    """
    if not section_embedding:
        return []
    try:
        rows = await client.execute_query(
            Q.SEMANTIC_SEARCH_CODE,
            {
                "query_vec": section_embedding,
                "repos": [repo_slug],
                "types": None,
                "layers": None,
                "top_k": top_k,
            },
        )
        return [{"id": r["id"], "name": r["name"], "filePath": r.get("filePath", "")}
                for r in rows]
    except Exception as e:
        logger.debug(f"Vector candidate lookup failed: {e}")
        return []


# ── LLM classification ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a code-documentation analyst.  Given a documentation section and a \
list of code symbol names, determine which symbols are actually explained, \
described, or documented in the section.  Return JSON only — no prose.

Output format:
{"documented": [{"name": "<symbol>", "confidence": <0.0 to 1.0>}, ...]}

Only include symbols that are genuinely documented in the section (confidence > 0.3).
If none qualify, return {"documented": []}.
"""


async def classify_with_llm(
    llm_client: LLMClient,
    model: str,
    section_text: str,
    candidates: list[dict],
) -> list[tuple[str, float]]:
    """Call the LLM to classify which candidates are documented in *section_text*.

    Args:
        llm_client: Shared LLM HTTP client.
        model: Model name (e.g. ``qwen2.5-coder:14b``).
        section_text: Section content (summary or heading).
        candidates: List of ``{id, name}`` dicts.

    Returns:
        List of ``(node_id, confidence)`` for candidates above ``_MIN_CONFIDENCE``.
        Falls back to ``(node_id, 0.5)`` for all candidates on parse failure.
    """
    if not candidates:
        return []

    name_to_id = {c["name"]: c["id"] for c in candidates}
    names_str = ", ".join(name_to_id.keys())
    user_msg = (
        f"Documentation section:\n\n{section_text[:1500]}\n\n"
        f"Candidate symbols: {names_str}"
    )

    try:
        raw = await llm_client.chat(
            path="/api/chat",
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(raw)
        documented = data.get("documented", [])
        results = []
        for item in documented:
            name = item.get("name", "")
            confidence = float(item.get("confidence", 0.0))
            node_id = name_to_id.get(name)
            if node_id and confidence >= _MIN_CONFIDENCE:
                results.append((node_id, confidence))
        return results

    except Exception as e:
        logger.warning(f"LLM classification failed: {e} — using fallback confidence 0.5")
        return [(c["id"], 0.5) for c in candidates]


# ── edge upsert ───────────────────────────────────────────────────────────────


async def _upsert_documents_edge(
    client: Neo4jClient,
    from_id: str,
    to_id: str,
    weight: float,
) -> bool:
    """Merge a DOCUMENTS edge between a Section and a CodeNode.

    Args:
        client: Neo4j client.
        from_id: Source (Section/Document) node ID.
        to_id: Target CodeNode ID.
        weight: Confidence weight (0.0–1.0).

    Returns:
        True if the edge was created/updated successfully.
    """
    try:
        rows = await client.execute_query(
            """
            MATCH (s) WHERE s.id = $from_id
            MATCH (n) WHERE n.id = $to_id
            MERGE (s)-[r:DOCUMENTS]->(n)
            ON CREATE SET r.weight = $weight, r.created_at = datetime()
            ON MATCH SET  r.weight = $weight, r.updated_at = datetime()
            RETURN r
            """,
            {"from_id": from_id, "to_id": to_id, "weight": weight},
        )
        return bool(rows)
    except Exception as e:
        logger.warning(f"Failed to upsert DOCUMENTS edge {from_id}→{to_id}: {e}")
        return False


# ── public entry point ────────────────────────────────────────────────────────


async def link_docs(
    client: Neo4jClient,
    settings: Settings,
    repo_slug: str,
    batch_size: int = 20,
    top_k_candidates: int = 10,
    use_embeddings: bool = True,
    min_confidence: float = _MIN_CONFIDENCE,
) -> DocsLinkerResult:
    """Infer and emit DOCUMENTS edges for all sections in a repository.

    For each Section node:
    1. Gather candidates via name-match + optional vector lookup.
    2. Call LLM to classify which candidates are actually documented.
    3. Emit DOCUMENTS edges for those above *min_confidence*.

    Args:
        client: Connected Neo4j client.
        settings: App settings (LLM provider config).
        repo_slug: Repository slug to scope the run.
        batch_size: Max sections processed per LLM call (not currently batched).
        top_k_candidates: Max vector-neighbour candidates per section.
        use_embeddings: Whether to supplement name-match with vector lookup.
        min_confidence: Minimum LLM confidence score to emit an edge.

    Returns:
        DocsLinkerResult with counts and any non-fatal errors.
    """
    result = DocsLinkerResult()
    llm_client = LLMClient(base_url=settings.summary.base_url)

    # ── Load sections and code names ─────────────────────────────────────────
    try:
        sections = await client.execute_query(
            Q.LOAD_SECTIONS_WITH_TEXT, {"repo": repo_slug}
        )
    except Exception as e:
        result.errors.append(f"Failed to load sections: {e}")
        return result

    if not sections:
        logger.info(f"No sections found for repo={repo_slug}")
        return result

    try:
        code_nodes = await client.execute_query(
            Q.LOAD_ALL_CODE_NAMES_FOR_REPO, {"repo": repo_slug}
        )
    except Exception as e:
        result.errors.append(f"Failed to load code nodes: {e}")
        return result

    logger.info(
        f"docs_linker: {len(sections)} sections, "
        f"{len(code_nodes)} code nodes for repo={repo_slug}"
    )

    # ── Process each section ──────────────────────────────────────────────────
    for section in sections:
        sec_id = section.get("id", "")
        sec_text = section.get("text") or section.get("name") or ""
        sec_embedding = section.get("embedding")

        if not sec_id or not sec_text.strip():
            continue

        try:
            # Candidate discovery
            name_candidates = find_candidates_by_name(sec_text, code_nodes)

            vec_candidates: list[dict] = []
            if use_embeddings and sec_embedding:
                vec_candidates = await find_candidates_by_vector(
                    client, sec_embedding, repo_slug, top_k_candidates
                )

            # Merge candidate lists, deduplicate by id
            seen_ids: set[str] = set()
            candidates: list[dict] = []
            for node in name_candidates + vec_candidates:
                if node["id"] not in seen_ids:
                    seen_ids.add(node["id"])
                    candidates.append(node)

            if not candidates:
                result.sections_processed += 1
                continue

            # LLM classification
            classified = await classify_with_llm(
                llm_client,
                settings.summary.model,
                sec_text,
                candidates,
            )

            # Emit DOCUMENTS edges
            for to_id, confidence in classified:
                if confidence < min_confidence:
                    result.edges_skipped += 1
                    continue
                ok = await _upsert_documents_edge(client, sec_id, to_id, confidence)
                if ok:
                    result.edges_written += 1
                else:
                    result.edges_skipped += 1

            result.sections_processed += 1

        except Exception as e:
            logger.warning(f"Failed to process section {sec_id}: {e}")
            result.errors.append(f"section {sec_id}: {e}")
            result.sections_processed += 1

    logger.info(
        f"docs_linker done: {result.sections_processed} sections, "
        f"{result.edges_written} DOCUMENTS edges written"
    )
    return result
