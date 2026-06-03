# Features

A full catalogue of what CodeKG does and how each piece works.

---

## 1. Multi-language AST extraction

CodeKG uses [tree-sitter](https://tree-sitter.github.io/tree-sitter/) to parse source files into typed graph nodes and edges — no regex, no heuristic string scanning.

| Language | What is extracted |
|----------|------------------|
| TypeScript / JavaScript | Files, Classes, Functions, `IMPORTS` edges, `CALLS` edges |
| C# | Files, Classes, Interfaces, Methods with full signature, `using`-based `IMPORTS`, `CALLS` edges attributed to containing method |
| Java | Files, Classes, Interfaces, Methods/Functions, `IMPORTS`, `CALLS` |
| Markdown | Documents, Sections (H1–H4), `HAS_SECTION`, `LINKS_TO`, `MENTIONS` edges |

Each function and method node carries:
- **`signature`** — the full parameter and return-type signature
- **`line_range`** — `[start, end]` line numbers in the file
- **`file_path`** — relative path to the source file within the repository
- **`nameTokens`** — camelCase-split tokens for better fulltext search (e.g. `ClientService` → `client service clientservice`)

> **Source code is not stored in the graph.** `codeSnippet` was removed to keep the graph lean — a large repo's method bodies can add tens of MB of duplicated text to Neo4j. Instead, use `file_path` + `line_range` to read source directly from the repository. At enrichment time, CodeKG reads code from disk automatically using the `source_path` recorded in the `:Repo` node.

---

## 2. Graph schema

### Node types

| Label | Description |
|-------|-------------|
| `:File` | A source file — TypeScript, C#, Java, or Markdown |
| `:Class` | A class or concrete type |
| `:Interface` | An interface or abstract contract |
| `:Function` | A function, method, constructor, or arrow function |
| `:Document` | A Markdown document |
| `:Section` | A heading section within a Markdown document |
| `:Layer` | An architectural layer node (service, controller, …) |
| `:Repo` | Repository metadata node |
| `:Module` | Unresolved import targets |

### Relationship types

| Relationship | From → To | Meaning |
|-------------|-----------|---------|
| `CALLS` | Function → Function | Direct call reference |
| `IMPORTS` | File → File/Module | Import / using directive |
| `DEFINES` | File/Class → Class/Function | Structural containment |
| `TESTS` | Function → Function | Test function covers production code (heuristic) |
| `MENTIONS` | Section → CodeNode | Doc section names a symbol |
| `DOCUMENTS` | Section → CodeNode | LLM-inferred: section describes this code |
| `HAS_SECTION` | Document/Section → Section | Document structure hierarchy |
| `LINKS_TO` | Section → Document | Markdown `[text](path)` links |
| `IN_REPO` | CodeNode → Repo | Ownership |
| `BELONGS_TO_LAYER` | CodeNode → Layer | Architectural layer membership |
| `RENAMED_FROM` | File → File | File rename tracking across commits |

---

## 3. Architectural layer assignment

Every class/interface is automatically tagged with an architectural layer based on its name suffix and file path. The heuristic runs at ingest time with no configuration needed.

| Layer | Name patterns | Path patterns |
|-------|--------------|---------------|
| `controller` | `*Controller` | `controllers/`, `routes/`, `handlers/` |
| `service` | `*Service` | `services/`, `service/` |
| `repository` | `*Repository`, `*Repo`, `*Dao` | `repositories/`, `repo/`, `dao/` |
| `model` | `*Model`, `*Entity`, `*Schema`, `*DTO` | `models/`, `entities/`, `schemas/` |
| `middleware` | `*Middleware`, `*Guard`, `*Filter`, `*Interceptor` | `middleware/`, `middlewares/` |
| `config` | `*Config`, `*Configuration`, `*Settings` | `config/`, `configuration/` |
| `utility` | `*Util`, `*Utils`, `*Helper` | `utils/`, `helpers/`, `lib/` |

Nodes that don't match any rule get `layer=NULL`.

---

## 4. LLM enrichment

After ingestion, run `code-kg enrich` to generate:

- **Summary** — a 1–3 sentence natural-language description of what the node does
- **Tags** — a list of semantic labels (e.g. `["authentication", "jwt", "guard"]`)
- **Complexity** — a rough 1–5 score for how complex the code is
- **Embedding vector** — a dense semantic vector for similarity search

Enrichment is **incremental** by default — only nodes without an existing summary are processed. Use `--limit` to process a batch at a time.

Supports two provider backends:
- **Ollama** (local, private) — default; uses `qwen2.5-coder:7b` (configurable via `SUMMARY__MODEL` in `.env`)
- **OpenAI** (cloud) — set `SUMMARY__PROVIDER=openai` and provide `SUMMARY__API_KEY`

---

## 5. Hybrid search

`find_nodes` combines two search strategies and merges results with **Reciprocal Rank Fusion (RRF)**:

1. **Fulltext search** — Neo4j Lucene index over `name`, `nameTokens`, `summary`, `signature`. Always runs, even without enrichment.
2. **Semantic vector search** — cosine similarity over `summary_embedding` vectors. Only runs when `use_semantic=true` and enrichment has been run.

Filters available: `repos`, `types` (node label), `layers`. All filters are additive (AND).

---

## 6. Call chain traversal

`trace_call_chain` uses Neo4j's `shortestPath` algorithm over `CALLS` edges to find the minimal call path between any two functions. Useful for:

- Tracing how a controller action reaches the database
- Finding all the hops between an API endpoint and a domain event
- Verifying that two modules don't have unexpected coupling

---

## 7. Impact analysis

`impact_analysis` returns the full blast radius of a node:

- **Upstream** — everything that calls this node (callers, depth-bounded)
- **Downstream** — everything this node calls (callees, depth-bounded)
- **Tests** — test functions linked via `TESTS` edges
- **Docs** — documentation sections that mention or document this node
- **Layer breakdown** — count of affected nodes per architectural layer

---

## 8. Test mapping

`code-kg test-map` (or the `run_test_map` MCP tool) infers `TESTS` edges using three weighted strategies:

| Strategy | How it works | Weight |
|----------|-------------|--------|
| CALLS graph | Test function has a `CALLS` path to production function | 0.6 |
| File-name heuristic | `candidate.service.spec.ts` → `candidate.service.ts` | 0.8 |
| Path mirror | `tests/services/Foo.test.cs` → `src/services/Foo.cs` | 0.7 |

Edges are only written when the combined confidence exceeds `min_weight` (default 0.5).

---

## 9. Documentation linking

`code-kg link-docs` (or `refresh_doc_links` MCP tool) infers `DOCUMENTS` edges from Markdown sections to code nodes using two passes:

1. **Name matching** — sections that mention a symbol name get a `MENTIONS` edge automatically during ingestion.
2. **LLM classification** — for each section, candidate code nodes are retrieved by name match and optionally vector similarity; the LLM then assigns a confidence score to each. Edges are written when confidence ≥ `min_confidence` (default 0.3).

---

## 10. Incremental ingestion

When triggered via `ingest_repo` with a `base_commit_sha`, CodeKG:

1. Calls `git diff` between the two commits
2. Extracts only the changed/added files
3. Runs the full parse → normalize → upsert pipeline on that subset
4. Soft-deletes nodes from deleted files (`deleted_at` timestamp)
5. Emits `RENAMED_FROM` edges for renamed files
6. Updates the `:Repo` node with the new commit SHA

This keeps large repos cheap to keep in sync — a 5-file commit doesn't re-parse 1 000 files.

---

## 11. MCP server

The MCP server exposes all read and write operations as [Model Context Protocol](https://modelcontextprotocol.io/) tools. It runs in two modes:

- **stdio** — for Claude Code / Claude Desktop (process-per-call, no port needed)
- **HTTP (streamable-http)** — for Copilot, custom agents, CI pipelines

See [MCP Setup Guide](mcp-setup.md) and [MCP Tools Reference](mcp-tools.md).

---

## 12. GitHub Actions integration

A ready-to-use workflow file for the DevOnHire example is at
`.github/workflows/code-kg-ingest.yml`. It:

- Triggers on push to `main`/`master` for source-file changes
- Resolves the last-ingested commit via `GET /api/repo-state`
- Posts an incremental `ingest_repo` call with the diff commit range
- Runs a non-blocking `refresh_summaries` step after successful ingest

---

## 13. Evaluation suite

`code-kg eval run --server http://localhost:8765` runs 10 end-to-end questions against the live MCP server and produces a Markdown report in `eval-results/`. The suite covers discovery, navigation, analysis, cross-domain, and guard/null-safety scenarios.
