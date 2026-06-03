---
name: codekg-refactor-impact
description: Pre-refactor risk assessment using CodeKG. Finds everything that calls a target node, how many tests cover the blast radius, and which layers are affected — so you know the risk before changing anything.
argument-hint: "<symbol to change, e.g. GetClient, ClientService, IRepository>"
allowed-tools: mcp_code-kg_find_nodes, mcp_code-kg_get_node, mcp_code-kg_impact_analysis, mcp_code-kg_find_tests_for, mcp_code-kg_trace_call_chain
---

# CodeKG: Refactor Impact Assessment

Use when the user asks "what breaks if I change X", "is it safe to refactor X", or "what calls X".

## Hard rules

1. CodeKG read tools only. Never call write tools.
2. Maximum 6 CodeKG tool calls.
3. Risk rating is REQUIRED in the output — do not omit it.

## Procedure

### Step 1 — Locate target (1–2 calls)

Call `find_nodes`:
- query: `$1`
- use_semantic: `false`
- limit: `10`

If `$1` is a class or interface, also include `types: ["Class", "Interface"]`.
If `$1` is a method, include `types: ["Function"]`.

Pick the single most-specific match. If multiple candidates share the same name, pick the one whose `file_path` best matches the user's context.

### Step 2 — Confirm target (1 call)

Call `get_node` on the target with `include_neighbors: true`.
Scan neighbours to identify:
- How many direct callers exist (upstream count)
- Whether this is an interface vs concrete implementation
- Which layer the target belongs to

### Step 3 — Full blast radius (1 call)

Call `impact_analysis`:
- `max_depth: 4` (deeper than default — refactoring risk requires full upstream picture)
- `include_tests: true`
- `include_docs: true`

Extract from result:
- `upstream`: everything that calls this node (direct + transitive callers)
- `layer_breakdown`: which layers are affected
- `tests`: existing test coverage
- `docs`: documentation that references this node (will need updating)

### Step 4 — Confirm critical paths (0–1 calls, only if needed)

If the target is called from a controller or public API entry point, call `trace_call_chain`
from that entry point to the target to confirm the public-facing exposure.
Skip this call if upstream already clearly shows the entry point.

### Step 5 — Risk rating

Compute risk using this rubric:

| Signal | Risk contribution |
|---|---|
| Upstream callers > 5 | High |
| Upstream callers cross 2+ layers | High |
| Called from controller (public API) | High |
| Is an interface or abstract contract | High (all implementors affected) |
| Upstream callers 2–5, same layer | Medium |
| Test coverage exists for all direct callers | Reduces risk one level |
| No upstream callers (leaf node) | Low |

Final rating: **High / Medium / Low**

## Output

```
## Target
[name, type, layer, file_path:line_range]
[one-line summary from graph]

## Risk Rating: [HIGH / MEDIUM / LOW]
[2–3 sentence justification referencing caller count, layers, test coverage]

## Blast Radius
**Direct callers ([N]):** [name, layer, file_path]
**Transitive callers ([N]):** [name, layer — summarise if > 5]
**Layers affected:** [from layer_breakdown]

## Test Coverage
**Tests covering this node:** [name, score]
**Callers with no test coverage:** [name — these are the unguarded change points]

## Documentation to update
[doc sections referencing this node — or "none found"]

## Recommended approach
[2–4 bullet points: e.g. "Add characterisation test before changing", "Interface change requires updating N implementors", "Safe to change — leaf node with full test coverage"]
```
