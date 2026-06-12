"""Domain models for code-kg."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# === AST Extraction (Raw) ===


class RawNode(BaseModel):
    """Raw node extracted by an IngestionSource, pre-normalization."""

    type: Literal[
        "file",
        "module",
        "class",
        "interface",
        "function",
        "method",
        "enum",
        "endpoint",
        "document",
        "section",
    ]
    name: str
    language: Optional[str] = None
    file_path: str
    repo: str
    line_range: Optional[tuple[int, int]] = None
    signature: Optional[str] = None
    code_snippet: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RawEdge(BaseModel):
    """Raw edge extracted by an IngestionSource, pre-normalization."""

    type: str
    from_id: str
    to_id: str
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# === Normalized (post ID assignment) ===


class NormalizedNode(BaseModel):
    """Node after stable ID assignment and enrichment."""

    id: str
    type: str
    name: str
    repo: str
    language: Optional[str] = None
    file_path: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    signature: Optional[str] = None
    signature_hash: Optional[str] = None
    file_hash: Optional[str] = None
    code_snippet: Optional[str] = None
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    summary_embedding: Optional[list[float]] = None
    complexity: Optional[str] = None
    layer: Optional[str] = None
    first_seen_commit: Optional[str] = None
    last_seen_commit: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedEdge(BaseModel):
    """Edge after normalization."""

    id: Optional[str] = None
    type: str
    from_id: str
    to_id: str
    weight: float = 1.0
    unresolved: bool = False
    first_seen_commit: Optional[str] = None
    last_seen_commit: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# === Enrichment Requests/Responses ===


class SummaryRequest(BaseModel):
    """Request to generate a summary for a node."""

    node_id: str
    node_type: str
    name: str
    language: Optional[str] = None
    code_or_text: str
    context: Optional[str] = None


class SummaryResponse(BaseModel):
    """Response from summary provider."""

    summary: str
    tags: list[str] = Field(default_factory=list)
    complexity: Literal["simple", "moderate", "complex"] = "moderate"


# === MCP Tool I/O Models ===


class FoundNode(BaseModel):
    """A found node in search results."""

    id: str
    type: str
    name: str
    file_path: Optional[str] = None
    summary: str
    tags: list[str] = Field(default_factory=list)
    layer: Optional[str] = None
    score: Optional[float] = None  # present in search results; None in structural queries


class NeighborRef(BaseModel):
    """Reference to a neighboring node."""

    id: str
    type: str
    name: str
    edge_type: str
    edge_weight: float


class FindNodesInput(BaseModel):
    """Input for find_nodes tool."""

    query: str
    types: Optional[list[str]] = None
    repos: Optional[list[str]] = None
    layers: Optional[list[str]] = None
    limit: int = 10
    use_semantic: bool = True


class FindNodesOutput(BaseModel):
    """Output for find_nodes tool."""

    nodes: list[FoundNode]
    total_matched: int


class GetNodeInput(BaseModel):
    """Input for get_node tool."""

    id: str
    include_neighbors: bool = True
    neighbor_edge_types: Optional[list[str]] = None
    max_neighbors: int = 20


class NodeDetail(BaseModel):
    """Detailed view of a node."""

    id: str
    type: str
    name: str
    summary: str
    tags: list[str]
    layer: Optional[str] = None
    file_path: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    signature: Optional[str] = None
    # code_snippet removed — code is not stored in the graph. Use file_path +
    # line_range to read source directly (avoids duplicating data in Neo4j).
    neighbors: list[NeighborRef] = Field(default_factory=list)


class ImpactAnalysisInput(BaseModel):
    """Input for impact_analysis tool."""

    id: str
    max_depth: int = 3
    include_tests: bool = True
    include_docs: bool = True


class ImpactPath(BaseModel):
    """A path in impact analysis."""

    node: FoundNode
    depth: int
    path_ids: list[str]


class ImpactAnalysisOutput(BaseModel):
    """Output for impact_analysis tool."""

    target: FoundNode
    upstream: list[ImpactPath]
    downstream: list[ImpactPath]
    tests: list[FoundNode] = Field(default_factory=list)
    docs: list[FoundNode] = Field(default_factory=list)
    layer_breakdown: dict[str, int] = Field(default_factory=dict)


class IngestRepoInput(BaseModel):
    """Input for ingest_repo write tool."""

    repo_url: str
    repo_slug: str
    commit_sha: str
    base_commit_sha: Optional[str] = None
    branch: str = "main"
    force_full: bool = False
    patterns: list[str] = Field(
        default=["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.cs", "**/*.md"],
        description="File glob patterns to ingest.",
    )


class IngestRepoOutput(BaseModel):
    """Output for ingest_repo write tool."""

    job_id: str
    status: Literal["completed", "queued", "failed"]
    nodes_added: int
    nodes_modified: int
    nodes_removed: int
    edges_added: int
    edges_removed: int
    duration_seconds: float
    errors: list[str] = Field(default_factory=list)


class ReindexFileInput(BaseModel):
    """Input for reindex_file write tool."""

    repo: str
    file_path: str
    commit_sha: Optional[str] = None


class RefreshEnrichmentInput(BaseModel):
    """Input for refresh_summaries / refresh_embeddings write tool."""

    repo: Optional[str] = None
    node_ids: Optional[list[str]] = None
    only_if_missing: bool = True
    limit: int = 500


class DeleteRepoInput(BaseModel):
    """Input for delete_repo write tool."""

    repo_slug: str
    confirm: bool = False  # must be True to execute; prevents accidental deletes


class RepoStateOutput(BaseModel):
    """State of a repo — returned by the /api/repo-state REST endpoint."""

    repo_slug: str
    last_commit_sha: Optional[str] = None
    last_ingest_at: Optional[str] = None
    node_count: int = 0
    exists: bool = False


# === New Read Tool I/O Models ===


class ProjectSummary(BaseModel):
    """Summary of a single ingested repository."""

    slug: str
    last_commit_sha: Optional[str] = None
    last_ingest_at: Optional[str] = None
    node_count: int
    edge_count: int


class ListProjectsOutput(BaseModel):
    """Output for list_projects tool."""

    projects: list[ProjectSummary]
    total: int


class GetArchitectureInput(BaseModel):
    """Input for get_architecture tool."""

    repo: str


class HotspotNode(BaseModel):
    """A high-degree node in the call graph."""

    id: str
    name: str
    type: str
    file_path: Optional[str] = None
    in_degree: int
    out_degree: int
    total_degree: int


class LayerFlow(BaseModel):
    """Cross-layer call flow."""

    from_layer: str
    to_layer: str
    edge_count: int


class ArchitectureOutput(BaseModel):
    """Output for get_architecture tool."""

    repo: str
    layers: list[dict]
    entry_points: list[dict]
    hotspots: list[HotspotNode]
    layer_flows: list[LayerFlow]


class FindDeadCodeInput(BaseModel):
    """Input for find_dead_code tool."""

    repo: str
    entry_layer: str = "controller"
    limit: int = 50


class DeadCodeOutput(BaseModel):
    """Output for find_dead_code tool."""

    repo: str
    entry_points_analyzed: int
    total_functions: int
    reachable_count: int
    unreachable_count: int
    unreachable_nodes: list[dict]
