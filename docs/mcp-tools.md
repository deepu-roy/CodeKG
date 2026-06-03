# MCP Tools Reference

CodeKG exposes 16 MCP tools plus one REST endpoint. All tools are available over
both the **stdio** and **HTTP (streamable-http)** transports.

See [MCP Setup Guide](mcp-setup.md) for how to connect your AI assistant or agent.

---

## Read tools

### `find_nodes`

Hybrid keyword + semantic search across the knowledge graph.

```
query         string    — search terms
repos         string[]  — filter to specific repo slugs (optional)
types         string[]  — filter to node labels: "Class", "Function", "Interface", "File", "Section" (optional)
layers        string[]  — filter to architectural layers: "service", "controller", "repository", … (optional)
limit         int       — max results (default 10)
use_semantic  bool      — include vector search (default true; requires enrichment)
```

**Returns** `{nodes: FoundNode[], total_matched: int}`

Each `FoundNode`:
```json
{
  "id":       "function:DevOnHire:Services/ClientService.cs:GetClient:95f9",
  "type":     "Function",
  "name":     "GetClient",
  "file_path": "DevOnHire.Services/ClientService.cs",
  "summary":  "Retrieves a client record by ID from the repository.",
  "tags":     ["client", "crud", "service"],
  "layer":    "service",
  "score":    1.109
}
```

**Example:**
```json
{ "query": "authentication guard", "layers": ["middleware"], "limit": 5 }
```

---

### `get_node`

Full details for a specific node — code body, signature, line range, and immediate neighbours.

```
id                   string    — stable node ID (from find_nodes)
include_neighbors    bool      — include neighbour list (default true)
neighbor_edge_types  string[]  — filter neighbours by edge type: "CALLS", "IMPORTS", "TESTS", … (optional)
max_neighbors        int       — max neighbours returned (default 20)
```

**Returns** `NodeDetail | null`

```json
{
  "id":        "function:DevOnHire:...:GetClient:95f9",
  "type":      "Function",
  "name":      "GetClient",
  "summary":   "Retrieves a client record by ID from the repository.",
  "tags":      ["client", "crud"],
  "layer":     "service",
  "file_path": "DevOnHire.Services/ClientService.cs",
  "line_range": [31, 41],
  "signature": "public async Task<ClientDTO> GetClient(int id)",
  "neighbors": [{ "id": "...", "name": "GetById", "edge_type": "CALLS", "edge_weight": 1.0 }]
}
```

> **Source code is not in the response.** `code_snippet` was removed from the graph to avoid duplicating potentially hundreds of MB of source text in Neo4j. Use `file_path` + `line_range` to read the code directly from the repository:</p>
>
> ```python
> lines = Path(repo_root, node["file_path"]).read_text().splitlines()
> code  = "\n".join(lines[node["line_range"][0]-1 : node["line_range"][1]])
> ```
```

---

### `impact_analysis`

Compute the blast radius of a node — who calls it, what it calls, which tests cover it, which docs reference it.

```
id             string  — target node ID
max_depth      int     — traversal depth for upstream/downstream (default 3)
include_tests  bool    — include TESTS-linked test functions (default true)
include_docs   bool    — include MENTIONS/DOCUMENTS doc sections (default true)
```

**Returns:**
```json
{
  "target":          { ...FoundNode },
  "upstream":        [{ "node": {...}, "depth": 1, "path_ids": [...] }],
  "downstream":      [{ "node": {...}, "depth": 1, "path_ids": [...] }],
  "tests":           [{ ...FoundNode }],
  "docs":            [{ ...FoundNode }],
  "layer_breakdown": { "controller": 2, "service": 1 }
}
```

**Typical use:** Before refactoring a service method, call `impact_analysis` to see all callers, tests, and docs that may need updating.

---

### `trace_call_chain`

Find the shortest call path between two functions.

```
from_id    string  — source node ID
to_id      string  — target node ID
max_depth  int     — maximum path length to search (default 6)
```

**Returns** `{ chain: NodeSummary[], depth: int } | null`

Returns `null` when no path exists within `max_depth`, or when `from_id == to_id`.

**Example result:**
```json
{
  "chain": [
    { "name": "Get",       "layer": "controller" },
    { "name": "GetClient", "layer": "service" },
    { "name": "GetById",   "layer": "repository" }
  ],
  "depth": 2
}
```

---

### `find_tests_for`

Return test functions that cover a given code node via `TESTS` edges.

```
id          string  — code node ID
min_weight  float   — minimum edge confidence (default 0.5)
```

**Returns** `FoundNode[]`

> Requires `code-kg test-map` or the `run_test_map` write tool to have been run first.

---

### `find_docs_for`

Return documentation sections that reference a code node via `MENTIONS` or `DOCUMENTS` edges.

```
id          string  — code node ID
min_weight  float   — minimum edge confidence (default 0.0 = all)
```

**Returns** `FoundNode[]` — Section and Document nodes, ordered by edge weight.

---

### `find_code_for`

Inverse of `find_docs_for` — start from a doc section and find the code it describes.

```
id          string  — Section or Document node ID
min_weight  float   — minimum edge confidence (default 0.0 = all)
```

**Returns** `FoundNode[]`

---

### `semantic_search`

Pure vector similarity search — useful when you want conceptually related nodes without knowing exact symbol names.

```
query   string    — natural language query
scope   string    — "code", "docs", or "all" (default "all")
top_k   int       — number of results (default 10)
repos   string[]  — optional repo filter
```

**Returns** `FoundNode[]`

> Requires enrichment (`code-kg enrich`) to populate embedding vectors.

---

### `list_layers`

Enumerate all detected architectural layers and their member counts.

```
repo                  string  — repo slug
include_member_count  bool    — include count of nodes per layer (default true)
```

**Returns** `[{ name: string, id: string, member_count: int }]`

---

### `get_diff`

Show what changed in the graph between two ingested commits.

```
repo          string  — repo slug
base_commit   string  — base commit SHA (must be ingested)
head_commit   string  — head commit SHA (must be ingested)
```

**Returns:**
```json
{
  "added":    [{ ...FoundNode }],
  "removed":  [{ ...FoundNode }],
  "modified": [{ ...FoundNode }],
  "summary":  "3 added, 1 removed, 5 modified"
}
```

---

## Write tools

> Write tools modify the graph. They use the same idempotent MERGE-based pipeline as the CLI.

### `ingest_repo`

Full or incremental ingest of a repository.

```
repo_url         string    — local path ("/path/to/repo") or GitHub https URL
repo_slug        string    — short identifier used as the node namespace
patterns         string[]  — glob patterns (default ["**/*.ts","**/*.cs","**/*.java"])
commit_sha       string    — current HEAD SHA (default "HEAD")
base_commit_sha  string    — if set, only files changed since this SHA are processed
force_full       bool      — force full re-ingest even if base_commit_sha is set (default false)
```

**Returns:**
```json
{
  "job_id":           "a3f8c12b4d91",
  "status":           "completed",
  "nodes_added":      902,
  "nodes_modified":   12,
  "nodes_removed":    0,
  "edges_added":      1615,
  "edges_removed":    0,
  "duration_seconds": 8.4,
  "errors":           []
}
```

---

### `reindex_file`

Re-extract and upsert a single file. Useful during development.

```
file_path  string  — absolute or repo-relative path to the file
repo       string  — repo slug
```

**Returns** `{ status, nodes_upserted, edges_upserted }`

---

### `refresh_summaries`

Regenerate LLM summaries and embeddings for nodes that are missing them (or all nodes if `only_if_missing=false`).

```
repo             string    — repo slug (optional; all repos if omitted)
limit            int       — max nodes to process (default 500)
only_if_missing  bool      — skip already-enriched nodes (default true)
node_ids         string[]  — process specific nodes by ID (optional)
```

**Returns** `{ status, enriched, written }`

---

### `run_test_map`

Infer and write `TESTS` edges for a repository.

```
repo_slug   string  — repo slug
min_weight  float   — minimum combined confidence (default 0.5)
```

**Returns** `{ status, edges_written, edges_skipped, errors }`

---

### `refresh_doc_links`

Infer and write `DOCUMENTS` edges from Markdown sections to code nodes.

```
repo_slug       string  — repo slug
use_embeddings  bool    — use vector similarity in candidate lookup (default true)
batch_size      int     — sections per LLM batch (default 20)
min_confidence  float   — minimum LLM confidence to emit an edge (default 0.3)
```

**Returns** `{ status, sections_processed, edges_written, edges_skipped, errors }`

---

### `delete_repo`

Hard-delete all nodes and edges for a repository. Irreversible.

```
repo_slug  string  — repo slug to delete
confirm    bool    — must be true to execute (safety gate)
```

**Returns** `{ status: "completed" | "aborted", nodes_deleted? }`

---

## REST endpoint

### `GET /api/repo-state`

Returns the current ingestion state of a repository. Used by the GitHub Actions
workflow to determine the `base_commit_sha` for incremental ingestion.

```
?slug=<repo_slug>
```

**Response:**
```json
{
  "repo_slug":       "DevOnHire",
  "last_commit_sha": "abc123def456",
  "last_ingest_at":  "2026-06-03T05:43:01Z",
  "node_count":      843,
  "exists":          true
}
```

Returns `{ "exists": false }` for unknown slugs.

---

## Node ID format

All tool calls that take an `id` parameter expect a **stable node ID** in this format:

```
<type>:<repo_slug>:<relative_file_path>:<name>[:<sig_hash>]
```

Examples:
```
class:DevOnHire:DevOnHire.Services/ClientService.cs:ClientService
function:DevOnHire:DevOnHire.Services/ClientService.cs:GetClient:95f9
file:DevOnHire:DevOnHire.WebApi/Controllers/ClientController.cs
```

The `<sig_hash>` suffix (4 hex chars) is added for overloaded methods to distinguish them.
Discover IDs by calling `find_nodes` first, then pass the returned `id` field to `get_node`, `impact_analysis`, etc.
