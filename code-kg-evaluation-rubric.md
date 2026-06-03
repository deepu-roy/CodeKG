# CodeGraphContext Bake-Off — Evaluation Rubric

**Time-box:** 5 working days
**Target repo:** https://github.com/deepu-roy/DevOnHire (Angular/TS + .NET/C# + markdown)
**Outcome:** A go/no-go decision on **Extend** vs **Fork** vs **Build from scratch**.

---

## 1. Setup & first ingest (Day 1, ~half day)

### Tasks
1. Install CodeGraphContext via pip in a clean venv. Capture every install issue.
2. Run the interactive setup wizard. Pick **Neo4j (Docker)** as the backend (not the default Kuzu — we want to evaluate the production target).
3. Index DevOnHire: `codegraphcontext index <path-to-DevOnHire>`.
4. Open Neo4j Browser at `localhost:7474` and run `MATCH (n) RETURN labels(n), count(n)` to inventory node types.
5. Run `codegraphcontext analyze callers AuthService` (or any service from DevOnHire).

### Pass criteria
- [ ] Install completes without manual patching.
- [ ] First ingest of DevOnHire finishes in under 10 minutes.
- [ ] Node counts are non-trivial (>500 nodes for DevOnHire).
- [ ] Both `.ts` and `.cs` files produce nodes.
- [ ] At least one `CALLS` (or equivalent) relationship is present.

### Score
- ✅ All pass → continue.
- ⚠️ 1–2 fail → continue but log as friction.
- ❌ 3+ fail → flag as a serious operational concern.

---

## 2. C#/.NET extraction depth (Day 1–2, ~1 day)

This is the highest-risk area. Most OSS tools focus on Python/TS first; C# support is uneven. **You will likely fork or build if this fails.**

### Tasks
1. Pick 3 representative .NET files from DevOnHire:
   - A Controller (e.g., `CandidateController.cs`)
   - A Service (e.g., `CandidateService.cs`)
   - A Repository / EF context file
2. For each, verify the graph contains:
   - The class as a `:Class` (or equivalent) node.
   - Every public method as a `:Method` node with parameters and return type captured.
   - `using` directives as `IMPORTS` (or equivalent) edges.
   - Method calls between methods as `CALLS` edges, resolved (not just text references).
3. For the controller specifically, check:
   - Are `[HttpGet]`, `[HttpPost]`, `[Route]` attributes captured as properties on the method node, or as separate `:Endpoint` nodes, or **not captured at all**?
   - Are route templates (`"api/candidates/{id}"`) extracted as strings somewhere queryable?
4. For dependency injection: does the graph show that `CandidateController` depends on `ICandidateService` (constructor parameter)?
5. Run a representative impact query: `MATCH (c:Class {name: 'CandidateService'})<-[:CALLS|DEPENDS_ON*1..3]-(caller) RETURN caller`.

### Pass criteria
- [ ] All public methods captured with correct signatures.
- [ ] Class inheritance and interface implementation captured.
- [ ] Method calls resolved (not just unresolved name references).
- [ ] HTTP attributes captured in some form (property, label, or separate node).
- [ ] Constructor-injected dependencies appear as edges.

### Score
- ✅ All pass → C# is solid, extend path is viable.
- ⚠️ HTTP attributes missing but methods/calls solid → small extension needed.
- ❌ Methods or calls weak → fork or build.

---

## 3. Angular/TypeScript extraction depth (Day 2, ~half day)

### Tasks
1. Pick 3 representative TS files:
   - A Component (e.g., `candidate-grid.component.ts`)
   - A Service (e.g., `candidate.service.ts`)
   - A Module (e.g., `app.module.ts`)
2. Verify:
   - Component class node exists with the `@Component` decorator captured as a property or relationship.
   - `selector`, `templateUrl`, `styleUrls` are queryable somewhere.
   - `@Injectable` services are distinguishable from plain classes.
   - `HttpClient.get/post/put/delete` calls are captured as `CALLS` edges to `HttpClient` methods (the URL string ideally captured too).
   - Module declarations (`declarations: [...]`, `imports: [...]`) produce relationships.
3. Check arrow functions and exported `const` functions are nodes, not just named function declarations.

### Pass criteria
- [ ] Component / service / module distinguishable (via label or property).
- [ ] Decorators captured in some form.
- [ ] `HttpClient` calls captured as edges, even if URL strings are not.
- [ ] Arrow functions in services produce nodes.

### Score
- ✅ All pass → TS extraction solid.
- ⚠️ Decorators missing → extension needed (small).
- ❌ Calls or component identification weak → fork or build.

---

## 4. Schema compatibility with the design doc (Day 2, ~half day)

### Tasks
1. Export the CodeGraphContext schema: `MATCH (n) UNWIND labels(n) AS l WITH DISTINCT l RETURN l`. Same for relationship types: `CALL db.relationshipTypes()`.
2. Compare against our design (§3.1 and §3.3 of the design doc):
   - Which of our planned labels exist already (`:File`, `:Class`, `:Function`, `:Method`)?
   - Which planned relationships exist (`CALLS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `CONTAINS`)?
   - What's named differently? (e.g., `:Function` vs `:FunctionDef`, `CALLS` vs `INVOKES`.)
   - What's missing entirely? (Layers, documents, sections, endpoints, concepts.)
3. Check stable ID strategy — what property uniquely identifies a node? Is it stable across re-ingest of the same commit?
4. Verify whether nodes carry properties like `summary`, `tags`, `embedding`, or if those are absent.

### Pass criteria
- [ ] Core code-graph labels and relationships exist with sensible naming.
- [ ] Nodes have stable IDs (test by re-running ingest and confirming IDs don't churn).
- [ ] Adding new labels (`:Document`, `:Layer`, `:Endpoint`) won't conflict with existing schema.

### Score
- ✅ Pass → schema is compatible, extension is purely additive.
- ⚠️ Naming differs but semantics align → adaptation layer needed in MCP tools (small).
- ❌ Schema fundamentally different (e.g., not property-graph-friendly, or labels collide) → fork.

---

## 5. MCP tool surface quality (Day 3, ~1 day)

### Tasks
1. Connect CodeGraphContext as an MCP server to Claude Code (per their docs).
2. Inventory available MCP tools — list every tool, its inputs, and its return shape.
3. Compare to our planned tool surface (§6.2 of design doc):

| Our planned tool | CGC equivalent? | Quality |
|---|---|---|
| `find_nodes` (hybrid keyword + vector) | ? | ? |
| `get_node` | ? | ? |
| `get_neighbors` | ? | ? |
| `impact_analysis` | ? | ? |
| `trace_call_chain` | ? | ? |
| `find_tests_for` | ? | ? |
| `find_docs_for` | ? | ? |
| `semantic_search` | ? | ? |
| `get_layer` | ? | ? |
| `diff_subgraph` | ? | ? |

4. For tools that exist, evaluate response token efficiency. Ask Claude Code "what calls CandidateService.create?" and measure the response size in tokens (rough char count / 4).
5. Verify whether tool responses use structured content (`structuredContent`) or just text.

### Pass criteria
- [ ] At least 6 of 10 planned tools have equivalents.
- [ ] Responses are structured, not raw Cypher dumps.
- [ ] Response sizes are reasonable (a typical impact query returns <5KB).
- [ ] Tools accept filters (by repo, by type) where useful.

### Score
- ✅ Pass → MCP surface is workable, add only what's missing.
- ⚠️ Many tools exist but responses are noisy → wrap with a thin shaping layer.
- ❌ Few tools or raw output only → consider whether to fork or use only as ingestion engine and build our own MCP layer on top.

---

## 6. Update mechanism (Day 3–4, ~half day)

### Tasks
1. Run `codegraphcontext watch <DevOnHire-path>`.
2. Modify a TS file (add a method to a service).
3. Confirm the change reflects in the graph within 30 seconds.
4. Modify a C# file (add a controller action). Confirm reflection.
5. Delete a file. Confirm node removal (or soft-delete).
6. Rename a file. Confirm rename handling (new node + old node? edge between them? lost references?).
7. Stop the watcher and try a manual ingest of a single commit range: is there a "since commit X" mode, or only full + watcher?

### Pass criteria
- [ ] File watcher updates the graph reliably.
- [ ] Deletions handled cleanly.
- [ ] CLI supports CI-style "ingest this commit" calls (for GitHub Actions integration).

### Score
- ✅ Pass → use file watcher locally, drive CI via CLI.
- ⚠️ Watcher works but no CI mode → wrap the CLI in our own GH Actions logic.
- ❌ Renames break references badly → significant work to make production-safe.

---

## 7. Extensibility (Day 4, ~1 day)

This is the linchpin. If CodeGraphContext is rigid, you're forking. If it's pluggable, you're extending.

### Tasks
1. Read the source code for the language-parser registration. How is a new language or new query pattern added? Is it a plugin interface, a config file, or a code change?
2. Try to add **one new node type** (`:Endpoint`) and **one new relationship** (`EXPOSES_ENDPOINT`) for C# controllers without modifying the upstream parser. Goal: extract `[HttpGet("api/candidates")]` and add it to the graph as an `:Endpoint` node connected to the controller method.
   - If you can do this via a plugin / config / post-processing hook → extension model works.
   - If you have to edit upstream Python files → forking territory.
3. Check whether embedding providers are pluggable. Can you swap their default to Ollama / a different model via config?
4. Check whether summary generation is part of the pipeline at all, and if so, whether the LLM provider is pluggable.

### Pass criteria
- [ ] New node/edge types can be added without forking upstream.
- [ ] Embedding provider is configurable (or absent and we add our own pass).
- [ ] Summary provider is configurable (or absent and we add our own pass).

### Score
- ✅ All pluggable → extend, contribute upstream.
- ⚠️ Some forking needed but on stable extension points → maintained fork is viable.
- ❌ Tightly coupled, no extension points → build, using CodeGraphContext only as reference architecture.

---

## 8. Cross-repo readiness (Day 4, ~half day)

### Tasks
1. Check if CodeGraphContext supports multiple repos in one graph. Does it tag nodes with a `repo` property, use separate databases per repo, or only support one repo at a time?
2. If multi-repo: index DevOnHire twice as "frontend" and "backend" namespaces. Verify cross-namespace queries work.
3. Search the codebase / docs for any concept of HTTP-edge inference between repos. (Almost certainly absent.)

### Pass criteria
- [ ] Multi-repo namespacing exists (or can be added trivially via a `repo` property pass).
- [ ] No fundamental blocker to cross-repo edges (e.g., they don't hard-isolate repos at the DB level).

### Score
- ✅ Pass → cross-repo HTTP edge inference is our work to add, but on top of a workable foundation.
- ⚠️ Single-repo only → significant rearchitecture needed for multi-repo, weakens the extend case.

---

## 9. Performance & ops sanity (Day 5, ~half day)

### Tasks
1. Measure full re-ingest time on DevOnHire. Compare to design doc target (<10 min).
2. Measure incremental re-ingest time after a single-file change (target: <30s).
3. Measure typical MCP tool call latency under realistic Claude Code use (target: <1s for most reads).
4. Inspect resource usage during steady-state file watching (memory, CPU).
5. Confirm that the Neo4j instance reaches a stable state — no runaway transactions, no unbounded query patterns.

### Pass criteria
- [ ] Full ingest within 2x of our budget.
- [ ] Incremental updates fast enough not to block dev workflow.
- [ ] MCP latency acceptable for interactive use.
- [ ] No obvious resource leaks.

---

## 10. Decision matrix (End of Day 5)

Tally the section scores:

| Sections passing | Recommendation |
|---|---|
| 8–10 ✅ | **Extend.** Add doc ingestion, cross-repo edges, layers as plugins/extensions. ~2 weeks of work vs. ~7 for a full build. |
| 5–7 ✅ | **Maintained fork.** Useful skeleton, but you'll diverge enough to need control. Budget ~3–4 weeks. |
| <5 ✅ | **Build from scratch** per the design doc. CodeGraphContext is useful as a reference for what to do (and what to avoid) but not as a foundation. |

Also note hard blockers — any of these collapses the extend path regardless of other scores:

- C# extraction is so weak it would require rewriting their C# parser.
- Schema is so different that our planned tools can't be expressed against it.
- No extension points — every change requires forking.
- License changes between now and adoption (CodeGraphContext is MIT today; pin the version you evaluate).

---

## 11. Parallel evaluation (optional, if time permits)

If you finish Day 1–2 ahead of schedule, run a **half-day spike on `techsavvyash/codegraph`** with the same Day 1 setup tasks. It uses Neo4j + Qdrant + OpenSearch + SCIP — heavier stack but potentially more precise. Decide whether to fold it into the deeper evaluation or drop it after the spike.

---

## 12. Deliverable

By end of Day 5, produce a 2-page memo containing:

1. **Recommendation** — Extend / Fork / Build, with a one-paragraph justification.
2. **Section scores** — table of all 10 sections.
3. **Gap list** — concrete features we'd need to build on top, with rough effort estimates.
4. **Risks** — license, maintenance, performance, language coverage.
5. **Next step** — if Extend or Fork: a revised phased plan replacing Phases 1–4 of the original design doc with adoption + extension milestones. If Build: confirm the original 7-phase plan stands.

Hand the memo + the original design doc to Claude Code with: *"Implement based on the chosen path. If Extend, the design doc is now an extension spec — Phases 1–4 are replaced by adopting CodeGraphContext, and Phases 5–7 carry forward as additive work."*
