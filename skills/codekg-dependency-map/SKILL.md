---
name: codekg-dependency-map
description: Low-cost CodeKG dependency map for one feature flow. Resolves an anchor node, confirms call paths, filters same-name noise, and returns a compact dependency report in under 8 calls.
argument-hint: "<flow phrase, e.g. create candidate>"
allowed-tools: mcp_code-kg_find_nodes, mcp_code-kg_get_node, mcp_code-kg_trace_call_chain, mcp_code-kg_impact_analysis, mcp_code-kg_find_tests_for, mcp_code-kg_find_docs_for
---

# CodeKG: Dependency Map

Use when the user asks to understand one feature flow and its dependencies.

## Hard rules

1. CodeKG tools only. Do not use file reading or other graph tools.
2. Maximum 8 CodeKG tool calls total.
3. Do NOT call any write tools (ingest_repo, run_test_map, refresh_summaries, etc.).
4. If write-tool data is missing (e.g. find_tests_for is empty), note it in output — do not trigger the write tool.

## Procedure

### Step 1 — Resolve anchor (1–2 calls)

Call `find_nodes`:
- query: `$1` plus any class/file context if the method name is generic
- types: `["Function", "Class"]`
- use_semantic: `false`
- limit: `10`

Pick ONE anchor using this priority:
1. Function with feature-specific name (e.g. `CreateCandidate`) in a controller or service file.
2. Function named generically (e.g. `Create`) only when its `file_path` contains the feature token.
3. If ambiguous after 2 calls, pick the best candidate and flag it.

### Step 2 — Confirm anchor (1 call)

Call `get_node` on the anchor with `neighbor_edge_types: ["CALLS"]`.
Use the CALLS neighbours to identify upstream entry point and downstream persistence boundary.

### Step 3 — Confirm paths (1–2 calls)

Call `trace_call_chain` for:
1. Entry point → anchor (or anchor → service if anchor is a controller action)
2. Anchor/service → repository or persistence boundary

Mark results as `confirmed`. Mark edges only seen in neighbours as `likely`.

### Step 4 — Full impact (1 call)

Call `impact_analysis` on the anchor or service node:
- `max_depth: 3`
- `include_tests: true`
- `include_docs: true`

This replaces separate find_tests_for and find_docs_for calls.
Only call find_tests_for/find_docs_for additionally if you need stricter filtering.

### Step 5 — Noise filter

Before output, apply:
- Keep nodes whose `file_path` contains the feature token from `$1`.
- Keep nodes that appear in a confirmed call chain regardless of file token.
- Mark same-name cross-feature nodes (e.g. `Create` in ClientService when mapping candidate flow) as `noise`.

## Output

```
## Confirmed Path
[ordered chain from trace_call_chain, one line per hop]

## Dependencies
**Direct (depth 1):** [names + file_path]
**Transitive (depth 2–3):** [names + file_path, marked likely/confirmed]
**Data Contracts:** [DTOs, interfaces, entities]

## Quality Links
**Tests:** [name, score — or "none found / test-map not run"]
**Docs:** [name — or "none found / link-docs not run"]

## Noise / Collisions
[node name: reason excluded, one line each]

## Cost
[N CodeKG calls used]
```

## Stop when

- At least one confirmed entry→service→persistence chain exists (or best partial chain documented).
- Tests and docs lookup completed (or absence explained).
- Noise list is explicit when generic-name collisions exist.
