---
name: codekg-test-gaps
description: Identifies untested functions and classes in a feature or layer using CodeKG. Shows which nodes have TESTS edges and which have none, producing a prioritised gap list.
argument-hint: "<feature name or layer, e.g. candidate service, repository layer>"
allowed-tools: mcp_code-kg_find_nodes, mcp_code-kg_get_node, mcp_code-kg_find_tests_for, mcp_code-kg_impact_analysis
---

# CodeKG: Test Gap Analysis

Use when the user asks "what's not tested in X", "which functions have no tests", "test coverage gaps for X", or "where are the missing tests".

## Hard rules

1. CodeKG read tools only. Never call run_test_map or any write tool.
2. If find_tests_for returns empty for ALL nodes, state that test-map has not been run and stop — do not invoke run_test_map.
3. Maximum 8 CodeKG tool calls. For features with many nodes, sample the most critical ones (service + repository layer) rather than checking every node.
4. Prioritise gaps by risk: public API and service layer gaps are higher risk than utility gaps.

## Procedure

### Step 1 — Scope the target nodes (1–2 calls)

If `$1` is a **feature name** (e.g. "candidate"):
  Call `find_nodes`:
  - query: `$1`
  - `layers: ["service", "repository"]`
  - `types: ["Function", "Class"]`
  - `use_semantic: false`
  - `limit: 15`

If `$1` is a **layer name** (e.g. "repository layer"):
  Call `find_nodes`:
  - `layers: [extracted_layer]`
  - `types: ["Function"]`
  - `use_semantic: false`
  - `limit: 20`

### Step 2 — Use impact_analysis as primary test signal (1 call)

Pick the most central node (typically the service class or main service method).
Call `impact_analysis`:
- `max_depth: 3`
- `include_tests: true`
- `include_docs: false`

Extract:
- Which downstream nodes appear in the `tests` field → **covered**
- Which downstream nodes do not appear → **potentially uncovered** (needs confirmation)

### Step 3 — Spot-check high-risk uncovered nodes (2–3 calls)

For up to 3 nodes identified as potentially uncovered in Step 2, call `find_tests_for`:
- `min_weight: 0.5`

A result with 0 items = confirmed gap.
A result with items = covered (update the covered list).

Do not call find_tests_for for every node — use impact_analysis as the primary signal
and only spot-check the most critical gaps.

### Step 4 — Prioritise gaps

Assign priority using:
- **Critical**: public method in service layer called from controller with no tests
- **High**: repository write method (Create, Update, Delete) with no tests
- **Medium**: repository read method with no tests
- **Low**: utility, helper, or private method with no tests

## Output

```
## Test Gap Report: [feature / layer]

### Coverage Summary
- Nodes checked: [N]
- Covered (have TESTS edges): [N]
- Gaps found: [N]
- Note: [if test-map was not run, state it here and skip the rest]

### Critical Gaps (fix first)
| Function | Layer | File | Risk reason |
|---|---|---|---|
[rows for critical priority gaps]

### High Priority Gaps
[same table format]

### Medium / Low Priority Gaps
[summarise as bullet list to save space]

### Well-Covered Areas
[brief note on what IS tested — important for confidence]

### Recommendation
[2–3 bullet points: e.g. "Focus integration tests on the Create→SaveChanges path", "Unit tests for service methods are the highest-value gap"]
```
