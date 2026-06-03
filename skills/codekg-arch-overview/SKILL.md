---
name: codekg-arch-overview
description: Architectural orientation of a codebase using CodeKG. Maps layers, key classes per layer, main entry points, and cross-cutting patterns — ideal for onboarding or understanding an unfamiliar repo.
argument-hint: "<repo slug, e.g. DevOnHire>"
allowed-tools: mcp_code-kg_list_layers, mcp_code-kg_find_nodes, mcp_code-kg_get_node, mcp_code-kg_semantic_search, mcp_code-kg_impact_analysis
---

# CodeKG: Architectural Overview

Use when the user asks "explain this codebase", "how is this repo structured", "onboard me to X", or "what's the architecture of X".

## Hard rules

1. CodeKG read tools only. Never call write tools.
2. Maximum 8 CodeKG tool calls.
3. Always start with `list_layers` — it is the cheapest and highest-signal first call.
4. Do not do a generic `find_nodes` sweep without a layer or type filter — it produces noise.

## Procedure

### Step 1 — Layer map (1 call)

Call `list_layers`:
- `repo: $1`
- `include_member_count: true`

This gives the architectural skeleton. Sort layers by member count descending.
Identify the top 3 most-populated layers — these are the core of the codebase.

### Step 2 — Key classes per layer (2–3 calls)

For each of the top 3 layers, call `find_nodes`:
- `repos: ["$1"]`
- `layers: [layer_name]`
- `types: ["Class", "Interface"]`
- `use_semantic: false`
- `limit: 5`

Pick the 2–3 most representative classes from each result (prefer those with summaries).

### Step 3 — Entry points (1 call)

Call `find_nodes`:
- `repos: ["$1"]`
- `layers: ["controller"]`
- `types: ["Class", "Function"]`
- `use_semantic: false`
- `limit: 8`

These are the public API surface. List them as the "interface to the outside world".

### Step 4 — Cross-cutting patterns (1 call, optional)

If the layer list includes `middleware`, `config`, or `utility`, call `find_nodes` once
for those combined to surface shared infrastructure:
- `repos: ["$1"]`
- `layers: ["middleware", "config"]`
- `types: ["Class"]`
- `use_semantic: false`
- `limit: 6`

### Step 5 — Validate a representative flow (1 call, optional)

Pick the most central service class and call `get_node` with `neighbor_edge_types: ["CALLS", "IMPORTS"]`
to illustrate one typical call path through the architecture.

## Output

```
## Architecture: [repo]

### Layer Map
| Layer | Node count | Purpose |
|---|---|---|
[one row per layer from list_layers, purpose inferred from node names]

### Core Layers (top 3 by size)

**[Layer 1 — e.g. service]**
- [ClassName]: [one-line summary or inferred purpose]
- [ClassName]: ...

**[Layer 2]**
...

**[Layer 3]**
...

### API Entry Points (controller layer)
[list of controller classes + key action methods]

### Cross-Cutting Infrastructure
[middleware, config, utility classes — or "not detected"]

### Representative Flow
[one example: ControllerAction → Service → Repository → DbContext]

### Observations
[2–4 bullet points: notable patterns, missing layers, unusually large layers, coupling observations]
```
