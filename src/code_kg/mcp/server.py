"""FastMCP server — all read + write tools, REST companion endpoint."""

import logging
from typing import Optional

from code_kg.config import Settings
from code_kg.domain.models import (
    DeleteRepoInput,
    FindNodesInput,
    GetNodeInput,
    ImpactAnalysisInput,
    IngestRepoInput,
    RefreshEnrichmentInput,
    ReindexFileInput,
)
from code_kg.graph.client import Neo4jClient
from code_kg.mcp.tools import read as read_tools
from code_kg.mcp.tools import write as write_tools
from code_kg.providers.embedding.base import build_embedding_provider

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    raise ImportError(
        "FastMCP is required. Install with: pip install mcp fastmcp"
    ) from e


def create_server(settings: Settings) -> "FastMCP":  # type: ignore[name-defined]
    """Build and return a configured FastMCP server instance.

    Registers:
    - 8 read tools  (find_nodes, get_node, impact_analysis, trace_call_chain,
                     find_tests_for, find_docs_for, find_code_for, semantic_search)
    - 3 Phase-5 tools (list_layers, get_diff, get_repo_state)
    - 6 write tools (ingest_repo, reindex_file, refresh_summaries, delete_repo,
                     run_test_map, refresh_doc_links)

    Also mounts a ``/api/repo-state`` REST GET endpoint that GitHub Actions
    can poll to retrieve the last-ingested commit SHA.

    Args:
        settings: Application settings (Neo4j, embedding, summary, MCP).

    Returns:
        Configured FastMCP instance.
    """
    mcp = FastMCP(
        "code-kg",
        dependencies=[],
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
    )

    graph = Neo4jClient(settings.neo4j)
    embedding_provider = build_embedding_provider(settings.embedding)

    async def _embed(text: str) -> Optional[list[float]]:
        """Best-effort embedding; returns None if provider fails."""
        try:
            return await embedding_provider.embed(text)
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # READ TOOLS
    # ═══════════════════════════════════════════════════════════════════════════

    # ── find_nodes ────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Search for code and documentation nodes using hybrid keyword + semantic search. "
            "Returns a ranked list of matching nodes with summaries and metadata. "
            "Use types=['Class','Function'] or layers=['service'] to narrow results. "
            "Searches name, nameTokens (camelCase-split), summary, and signature.\n\n"
            "EFFICIENCY TIPS:\n"
            "- Set use_semantic=false for exact name lookups (faster, no embeddings needed).\n"
            "- For generic method names (Create, Update, Delete) that exist across many features, "
            "include the class or file context in the query "
            "(e.g. 'CreateCandidate CandidateService' not just 'Create') to avoid same-name "
            "collisions from other features.\n"
            "- Limit exploratory calls to 1-2 max; if results are ambiguous, tighten the query "
            "rather than calling again with broad terms.\n"
            "- After finding a node ID here, use get_node or impact_analysis — do not call "
            "find_nodes again for the same feature."
        ),
    )
    async def find_nodes(
        query: str,
        types: Optional[list[str]] = None,
        repos: Optional[list[str]] = None,
        layers: Optional[list[str]] = None,
        limit: int = 10,
        use_semantic: bool = True,
    ) -> dict:
        """Hybrid keyword + vector search across the code knowledge graph."""
        await graph.connect()
        inp = FindNodesInput(
            query=query, types=types, repos=repos,
            layers=layers, limit=limit, use_semantic=use_semantic,
        )
        embedding = await _embed(query) if use_semantic else None
        result = await read_tools.find_nodes(graph, inp, query_embedding=embedding)
        return result.model_dump()

    # ── get_node ──────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Get full details for a specific node by its stable ID, including "
            "its summary, signature, file_path, line_range, and immediate neighbours. "
            "Source code is NOT stored in the graph — use file_path + line_range "
            "to read the code directly from the repository. "
            "Use find_nodes first to discover node IDs.\n\n"
            "EFFICIENCY TIPS:\n"
            "- Pass neighbor_edge_types=['CALLS'] to get only call-graph neighbours "
            "and skip structural edges (IN_REPO, BELONGS_TO_LAYER) you don't need.\n"
            "- Use this as a lightweight anchor check (1 call) before running "
            "impact_analysis. If the node summary + neighbours already answer the "
            "question, skip impact_analysis entirely."
        ),
    )
    async def get_node(
        id: str,
        include_neighbors: bool = True,
        neighbor_edge_types: Optional[list[str]] = None,
        max_neighbors: int = 20,
    ) -> Optional[dict]:
        """Get a single node with its immediate graph neighbourhood."""
        await graph.connect()
        inp = GetNodeInput(
            id=id, include_neighbors=include_neighbors,
            neighbor_edge_types=neighbor_edge_types, max_neighbors=max_neighbors,
        )
        result = await read_tools.get_node(graph, inp)
        return result.model_dump() if result else None

    # ── impact_analysis ───────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Analyse the blast radius of a code node — who calls it (upstream), "
            "what it depends on (downstream), which tests cover it, and which "
            "docs reference it. Returns a layer breakdown of affected nodes.\n\n"
            "This is the MOST COST-EFFICIENT single call for dependency mapping. "
            "With include_tests=true and include_docs=true (both default), it returns "
            "everything find_tests_for and find_docs_for would return in the same call. "
            "DO NOT call find_tests_for or find_docs_for separately after impact_analysis "
            "unless you need stricter filtering (e.g. higher min_weight) than what "
            "impact_analysis provides.\n\n"
            "RECOMMENDED WORKFLOW for feature dependency mapping (5-6 calls total):\n"
            "1. find_nodes — locate the anchor node ID (1-2 calls)\n"
            "2. get_node — confirm anchor and see immediate neighbours (1 call)\n"
            "3. trace_call_chain — confirm entry→service and service→persistence paths (1-2 calls)\n"
            "4. impact_analysis — full upstream/downstream/tests/docs in one shot (1 call)\n"
            "Only add find_tests_for or find_docs_for if impact_analysis results are insufficient."
        ),
    )
    async def impact_analysis(
        id: str,
        max_depth: int = 3,
        include_tests: bool = True,
        include_docs: bool = True,
    ) -> Optional[dict]:
        """Impact analysis: upstream callers, downstream callees, tests, docs."""
        await graph.connect()
        inp = ImpactAnalysisInput(
            id=id, max_depth=max_depth,
            include_tests=include_tests, include_docs=include_docs,
        )
        result = await read_tools.impact_analysis(graph, inp)
        return result.model_dump() if result else None

    # ── trace_call_chain ──────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Find the shortest call path between two code nodes. "
            "Returns the chain of intermediate nodes and the path length. "
            "Only edges that exist in the CALLS graph are traversed — this is the "
            "ground-truth confirmation of a dependency path.\n\n"
            "EFFICIENCY TIPS:\n"
            "- Call AFTER impact_analysis to confirm specific paths it suggests. "
            "Edges in impact_analysis upstream/downstream are likely dependencies; "
            "trace_call_chain confirms whether a direct CALLS path actually exists.\n"
            "- Limit to 2 calls per feature: entry→service and service→persistence boundary. "
            "Additional calls are rarely needed.\n"
            "- Returns null when no path exists within max_depth — not an error."
        ),
    )
    async def trace_call_chain(
        from_id: str,
        to_id: str,
        max_depth: int = 6,
    ) -> Optional[dict]:
        """Trace the shortest call chain between two nodes."""
        await graph.connect()
        return await read_tools.trace_call_chain(graph, from_id, to_id, max_depth)

    # ── find_tests_for ────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Find test functions or methods that exercise a given code node. "
            "Tests are linked via TESTS edges weighted by heuristic confidence "
            "(run code-kg test-map to generate these edges).\n\n"
            "NOTE: impact_analysis already returns tests when include_tests=true (default). "
            "Only call this tool when you need stricter filtering than impact_analysis "
            "provides — for example min_weight > 0.5 — or when focusing on tests alone "
            "without a full dependency traversal."
        ),
    )
    async def find_tests_for(
        id: str,
        min_weight: float = 0.5,
    ) -> list[dict]:
        """Return test nodes linked to a target by TESTS edges."""
        await graph.connect()
        nodes = await read_tools.find_tests_for(graph, id, min_weight)
        return [n.model_dump() for n in nodes]

    # ── find_docs_for ─────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Find documentation sections or documents that reference a code node "
            "via MENTIONS (exact name match) or DOCUMENTS (LLM-inferred) edges. "
            "Run code-kg link-docs to populate DOCUMENTS edges.\n\n"
            "NOTE: impact_analysis already returns docs when include_docs=true (default). "
            "Only call this tool when you need deeper doc results than impact_analysis "
            "provides — for example a lower min_weight threshold or doc-only focus."
        ),
    )
    async def find_docs_for(
        id: str,
        min_weight: float = 0.0,
    ) -> list[dict]:
        """Return doc nodes (Section/Document) linked to a code node."""
        await graph.connect()
        nodes = await read_tools.find_docs_for(graph, id, min_weight)
        return [n.model_dump() for n in nodes]

    # ── find_code_for ─────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Find code nodes that a documentation section references via "
            "MENTIONS or DOCUMENTS edges. Inverse of find_docs_for — "
            "start from a doc section and discover the code it describes."
        ),
    )
    async def find_code_for(
        id: str,
        min_weight: float = 0.0,
    ) -> list[dict]:
        """Return code nodes linked from a doc section."""
        await graph.connect()
        nodes = await read_tools.find_code_for(graph, id, min_weight)
        return [n.model_dump() for n in nodes]

    # ── semantic_search ───────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Pure semantic (vector) search — useful when you want to find "
            "conceptually similar nodes using natural-language phrasing rather than "
            "exact symbol names. Use scope='code', 'docs', or 'all'. "
            "Requires enrichment to have been run (code-kg enrich)."
        ),
    )
    async def semantic_search(
        query: str,
        scope: str = "all",
        top_k: int = 10,
        repos: Optional[list[str]] = None,
    ) -> list[dict]:
        """Vector-only semantic search across code and/or documentation."""
        await graph.connect()
        embedding = await _embed(query)
        if not embedding:
            return []
        nodes = await read_tools.semantic_search(graph, embedding, scope, top_k, repos)
        return [n.model_dump() for n in nodes]

    # ── list_layers ───────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "List all architectural layers detected in a repository. "
            "Layers are inferred from directory names and class suffixes "
            "(e.g. service, repository, controller, model)."
        ),
    )
    async def list_layers(
        repo: str,
        include_member_count: bool = True,
    ) -> list[dict]:
        """Return all layers for a repo with optional member counts."""
        await graph.connect()
        from code_kg.graph import queries as Q
        rows = await graph.execute_query(Q.LIST_LAYERS, {"repo": repo})
        result = []
        for r in rows:
            item: dict = {"name": r.get("name"), "id": r.get("id")}
            if include_member_count:
                cnt_rows = await graph.execute_query(
                    Q.GET_LAYER_MEMBERS,
                    {"layer_id": r["id"], "limit": 9999},
                )
                item["member_count"] = len(cnt_rows)
            result.append(item)
        return result

    # ── get_diff ──────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Show what changed in the graph between two commits — which nodes "
            "were added, removed, or modified. Requires both commits to have been "
            "ingested. Useful for code-review and changelog generation."
        ),
    )
    async def get_diff(
        repo: str,
        base_commit: str,
        head_commit: str,
    ) -> dict:
        """Return added / removed / modified nodes between two commits."""
        await graph.connect()
        from code_kg.graph import queries as Q
        from code_kg.mcp.shaping import shape_node_for_list
        params = {"repo": repo, "base_commit": base_commit, "head_commit": head_commit}
        added_rows = await graph.execute_query(Q.DIFF_ADDED, {**params})
        removed_rows = await graph.execute_query(Q.DIFF_REMOVED, {**params})
        modified_rows = await graph.execute_query(Q.DIFF_MODIFIED, {**params})
        return {
            "added":    [shape_node_for_list(r) for r in added_rows],
            "removed":  [shape_node_for_list(r) for r in removed_rows],
            "modified": [shape_node_for_list(r) for r in modified_rows],
            "summary": (
                f"{len(added_rows)} added, {len(removed_rows)} removed, "
                f"{len(modified_rows)} modified"
            ),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # WRITE TOOLS
    # ═══════════════════════════════════════════════════════════════════════════

    # ── ingest_repo ───────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "⚠️ ADMIN/CI TOOL — MODIFIES THE GRAPH. "
            "Do NOT call this during code analysis, dependency mapping, or any read-only query. "
            "Only call when the user explicitly asks to ingest or update a repository.\n\n"
            "Ingest or update a repository in the knowledge graph. "
            "Supports local paths and GitHub https:// URLs. "
            "Pass base_commit_sha for incremental update (only changed files); "
            "omit it or set force_full=true for a full re-ingest. "
            "Used by CI workflows on every push."
        ),
    )
    async def ingest_repo(
        repo_url: str,
        repo_slug: str,
        commit_sha: str,
        base_commit_sha: Optional[str] = None,
        branch: str = "main",
        force_full: bool = False,
        patterns: Optional[list[str]] = None,
    ) -> dict:
        """Ingest or incrementally update a repository."""
        await graph.connect()
        inp = IngestRepoInput(
            repo_url=repo_url,
            repo_slug=repo_slug,
            commit_sha=commit_sha,
            base_commit_sha=base_commit_sha,
            branch=branch,
            force_full=force_full,
            patterns=patterns or ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx",
                                   "**/*.cs", "**/*.md"],
        )
        result = await write_tools.ingest_repo(graph, settings, inp)
        return result.model_dump()

    # ── reindex_file ──────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "⚠️ ADMIN/CI TOOL — MODIFIES THE GRAPH. "
            "Do NOT call this during code analysis or read-only queries. "
            "Only call when the user explicitly asks to re-index a specific file.\n\n"
            "Re-extract and upsert a single file without running a full ingest. "
            "Useful during development when you've modified one file and want "
            "the graph to reflect the change immediately."
        ),
    )
    async def reindex_file(
        repo: str,
        file_path: str,
        commit_sha: Optional[str] = None,
    ) -> dict:
        """Refresh a single file in the graph."""
        await graph.connect()
        inp = ReindexFileInput(repo=repo, file_path=file_path, commit_sha=commit_sha)
        return await write_tools.reindex_file(graph, inp)

    # ── refresh_summaries ─────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "⚠️ ADMIN/CI TOOL — TRIGGERS LLM CALLS AND MODIFIES THE GRAPH. "
            "Do NOT call this during code analysis or read-only queries. "
            "Only call when the user explicitly asks to regenerate summaries or embeddings.\n\n"
            "Re-run LLM summary and embedding generation for nodes that are "
            "missing enrichment data. Run after upgrading the summary model or "
            "after ingesting new files without enrichment. "
            "Set only_if_missing=false to force re-enrichment of all nodes."
        ),
    )
    async def refresh_summaries(
        repo: Optional[str] = None,
        node_ids: Optional[list[str]] = None,
        only_if_missing: bool = True,
        limit: int = 500,
    ) -> dict:
        """Re-run enrichment (summaries + embeddings) for matching nodes."""
        await graph.connect()
        inp = RefreshEnrichmentInput(
            repo=repo, node_ids=node_ids,
            only_if_missing=only_if_missing, limit=limit,
        )
        return await write_tools.refresh_summaries(graph, settings, inp)

    # ── delete_repo ───────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "⚠️ ADMIN TOOL — PERMANENTLY DESTRUCTIVE. CANNOT BE UNDONE. "
            "Do NOT call this during code analysis or any read-only workflow. "
            "Only call when the user explicitly asks to delete a repository from the graph. "
            "Must pass confirm=true to execute.\n\n"
            "Hard-deletes all nodes and edges for a repository from the graph."
        ),
    )
    async def delete_repo(
        repo_slug: str,
        confirm: bool = False,
    ) -> dict:
        """Hard-delete a repository's entire namespace from the graph."""
        await graph.connect()
        inp = DeleteRepoInput(repo_slug=repo_slug, confirm=confirm)
        return await write_tools.delete_repo(graph, inp)

    # ── run_test_map ──────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "⚠️ ADMIN/CI TOOL — MODIFIES THE GRAPH. "
            "Do NOT call this because find_tests_for returned empty results. "
            "If find_tests_for is empty, it means test-map has not been run yet — "
            "report this to the user and ask them to run 'code-kg test-map' from the CLI. "
            "Only call this tool when the user explicitly asks to run or refresh test mapping.\n\n"
            "Infer TESTS edges from test functions to the production code they exercise. "
            "Uses three strategies: CALLS graph (weight 0.9), file-name heuristic (0.7), "
            "and path mirroring (0.6). Run after ingestion to enable find_tests_for."
        ),
    )
    async def run_test_map(
        repo: str,
        min_weight: float = 0.5,
    ) -> dict:
        """Infer and emit TESTS edges for a repository."""
        await graph.connect()
        return await write_tools.run_test_map(graph, repo, min_weight)

    # ── refresh_doc_links ─────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "⚠️ ADMIN/CI TOOL — TRIGGERS LLM CALLS AND MODIFIES THE GRAPH. "
            "Do NOT call this because find_docs_for returned empty results. "
            "If find_docs_for is empty, report to the user that doc links have not been "
            "generated yet and ask them to run 'code-kg link-docs' from the CLI. "
            "Only call this tool when the user explicitly asks to refresh documentation links.\n\n"
            "Infer DOCUMENTS edges from documentation sections to code nodes using the LLM. "
            "Supplements exact name-match with vector-neighbour lookup when embeddings exist. "
            "Run after code-kg enrich to enable find_docs_for with high-confidence links."
        ),
    )
    async def refresh_doc_links(
        repo: str,
        use_embeddings: bool = True,
        batch_size: int = 20,
        min_confidence: float = 0.3,
    ) -> dict:
        """Infer DOCUMENTS edges from doc sections to code nodes."""
        await graph.connect()
        return await write_tools.refresh_doc_links(
            graph, settings, repo, use_embeddings, batch_size, min_confidence
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # REST COMPANION ENDPOINT  (/api/repo-state)
    # Used by GitHub Actions to fetch the last-ingested commit SHA before
    # calling ingest_repo so incremental diffing works correctly.
    # ═══════════════════════════════════════════════════════════════════════════

    @mcp.custom_route("/api/repo-state", methods=["GET"])
    async def repo_state_endpoint(request) -> dict:  # type: ignore[type-arg]
        """GET /api/repo-state?slug=<repo_slug>

        Returns JSON: { repo_slug, last_commit_sha, last_ingest_at, node_count, exists }
        """
        from starlette.responses import JSONResponse
        slug = request.query_params.get("slug", "")
        if not slug:
            return JSONResponse({"error": "slug query param required"}, status_code=400)
        await graph.connect()
        state = await write_tools.get_repo_state(graph, slug)
        return JSONResponse(state.model_dump())

    return mcp
