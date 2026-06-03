---
name: codekg-find-entrypoint
description: Traces a feature from its HTTP entry point down to the database boundary using CodeKG. Confirms the full controller → service → repository → persistence path and identifies the contracts at each layer boundary.
argument-hint: "<feature name, e.g. candidate login, create vacancy>"
allowed-tools: mcp_code-kg_find_nodes, mcp_code-kg_get_node, mcp_code-kg_trace_call_chain, mcp_code-kg_impact_analysis
---

# CodeKG: Find Entry Point and Trace Feature Path

Use when the user asks "where does X start", "how does X reach the database", "trace the flow for X", or "what's the call path for X".

## Hard rules

1. CodeKG read tools only. Never call write tools.
2. Maximum 7 CodeKG tool calls.
3. Anchor at the controller layer first, then trace downward — do not start from the middle.
4. Generic method names (Get, Create, Update, Delete) MUST be scoped to the feature file path — never report generic methods as the entry point without file path confirmation.

## Procedure

### Step 1 — Find controller entry point (1–2 calls)

Call `find_nodes`:
- query: `$1`
- `repos`: repo slug if known
- `layers: ["controller"]`
- `types: ["Function", "Class"]`
- `use_semantic: false`
- `limit: 8`

If no controller result, broaden to `types: ["Class", "Function"]` without layer filter
but add `$1` tokens to query to stay feature-scoped.

Pick the most specific controller action (e.g. `CreateCandidate` over `Create`).
Verify the `file_path` contains the feature token before selecting.

### Step 2 — Find persistence boundary (1 call)

Call `find_nodes`:
- query: `$1` + "save create async repository"
- `layers: ["repository"]`
- `types: ["Function"]`
- `use_semantic: false`
- `limit: 6`

Identify the write boundary: typically `CreateAsync`, `SaveChangesAsync`, or `Add`.

### Step 3 — Confirm controller → service path (1 call)

Call `trace_call_chain`:
- `from_id`: controller action ID
- `to_id`: service method ID (from Step 1 neighbours if available, else find_nodes service layer)
- `max_depth: 3`

### Step 4 — Confirm service → persistence path (1 call)

Call `trace_call_chain`:
- `from_id`: service method ID
- `to_id`: repository/persistence method ID
- `max_depth: 4`

### Step 5 — Extract contracts at boundaries (1 call)

Call `get_node` on the service method to extract:
- Interface it implements (the contract the controller depends on)
- Parameters (input DTO)
- Return type (output DTO)

### Step 6 — Noise filter

Exclude any node in the path whose `file_path` does not contain the feature token from `$1`,
unless it is a shared base class (`BaseRepository`, `ApplicationDbContext`) — those are
infrastructure and should be marked as such, not excluded.

## Output

```
## Feature Path: [feature name]

### Confirmed Call Chain
[HTTP Entry] → [Service] → [Repository] → [Persistence]
[ControllerClass.Method (file:line)] 
  → [ServiceClass.Method (file:line)]
  → [RepoClass.Method (file:line)]
  → [DbContext.SaveChanges (file:line)]

Confidence: confirmed / partial (note which hops are confirmed vs inferred)

### Layer Contracts
**Controller → Service contract:** [interface name, method signature]
**Service → Repository contract:** [interface name, method signature]
**Input DTO:** [DTO class name]
**Output DTO:** [DTO class name]

### Shared Infrastructure in Path
[BaseRepository, ApplicationDbContext, etc. — noted as shared, not feature-specific]

### Path not found / gaps
[any hop that trace_call_chain could not confirm — with likely reason]
```
