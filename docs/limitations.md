# Known Limitations

Understanding what CodeKG does not do helps you plan where to complement it with
other tools or manual effort.

---

## Language support

**Currently supported:** TypeScript, JavaScript, C#, Java, Markdown

**Not yet supported:** Python, Go, Rust, Ruby, Kotlin, Swift, PHP, and others.

Adding a new language requires a tree-sitter grammar and a new source module
(see the contributor guide in the main README). Python and Go are the planned
next additions.

Markdown is parsed for documentation cross-linking only — it does not produce
executable-code nodes.

---

## Call resolution accuracy

### Cross-file calls are marked `unresolved`

CodeKG resolves same-file calls precisely. For cross-file calls, it matches by
symbol name. When two functions share the same name in different files, the edge
is emitted as `unresolved=true` and the resolution may be ambiguous.

The current symbol registry is built per-file, so calls to overloaded methods
across namespaces may be attributed to the wrong target.

### No dynamic dispatch

Interface method calls, virtual dispatch, dependency injection, and event-based
coupling are **not tracked**. If `IClientService.GetClient()` is called via a
DI container, CodeKG records only the interface call — not which concrete
implementation is invoked at runtime.

### No reflection or metaprogramming

Dynamic calls via `Activator.CreateInstance`, `MethodInfo.Invoke`, `eval`, or
decorator-injected methods are invisible to the AST parser.

### Decorators / annotations as call sites

Angular `@Injectable()` and ASP.NET `[HttpGet]` are parsed as metadata but do
not produce `CALLS` edges to the framework plumbing (e.g. the DI container, the
routing pipeline).

---

## Layer assignment

Layer detection is heuristic: it matches class name suffixes and directory names.
It does not:

- Read custom annotations or attributes (e.g. `[Controller]`)
- Account for multi-layer classes (a class that is both a service and a model)
- Detect layers outside the built-in seven (`controller`, `service`, `repository`,
  `model`, `middleware`, `config`, `utility`)

Nodes that don't match any rule get `layer=NULL`.

---

## Test mapping

`TESTS` edges are inferred from three heuristics: CALLS graph, file-name
similarity, and path mirroring. This means:

- **False positives** are possible when file names are similar but test different things
- **False negatives** occur for tests that call helpers rather than the function directly
- Integration tests that span multiple services are usually not linked to any single function
- The minimum confidence threshold (`min_weight=0.5` by default) is a tunable but
  not a guarantee

---

## Documentation linking

`DOCUMENTS` edges are inferred by LLM classification. Quality depends on:

- The LLM's understanding of the codebase terminology
- Whether the doc section has enough text for the LLM to reason about
- The `min_confidence` threshold (default 0.3 — deliberately low to maximise recall)

Very short sections (< 10 words) are often misclassified. Sections that use
domain jargon not present in symbol names may produce no matches.

---

## Incremental ingestion

Incremental ingestion (`base_commit_sha`) works on file-level diffs:
- Only changed/added/deleted files are re-parsed
- Cross-file symbol resolution is re-run only for the changed file set;
  calls from _other_ files to the changed file are not re-resolved

This means that a rename of a widely-called function will produce correct
`RENAMED_FROM` edges and soft-delete the old nodes, but CALLS edges from
unchanged files still point to the old node ID until those files are next
re-ingested.

---

## Performance at scale

| Scenario | Practical limit |
|----------|----------------|
| Single-repo ingest | Works well up to ~5 000 source files |
| Total nodes in graph | Neo4j Community handles ~500 k nodes comfortably |
| Semantic search | Degrades for > 100 k nodes without Neo4j Enterprise vector index tuning |
| Enrichment throughput | ~60–200 nodes/hour on local Ollama; ~1 000+/hour on OpenAI |

Very large monorepos (> 10 000 files) may require running ingestion in pattern-scoped batches rather than a single pass.

---

## Embedding model lock-in

The vector index is created at `bootstrap` time with the dimension of the
configured embedding model. Changing the model (e.g. from 384-dim `bge-small`
to 1536-dim `text-embedding-3-small`) requires:

1. Dropping and recreating the Neo4j vector index
2. Re-running `code-kg enrich` for all repos to regenerate vectors

There is no automated migration path for this today.

---

## Source code not stored in the graph

`codeSnippet` is intentionally not persisted in Neo4j. Enrichment reads code
directly from the source files at enrichment time using `file_path` + `line_range`
stored on each node. The absolute repo root is stored in `:Repo.source_path` at
ingest time and used automatically.

**Consequences:**
- Enrichment requires the source files to be accessible (volume-mounted in Docker)
  at the path recorded during ingest. If you move the repo, re-ingest to update
  `source_path`.
- The `get_node` MCP tool does not return source code. Consumers read it via
  `file_path` + `line_range` directly from the repository.
- If a node has no `line_range` (e.g. some File nodes), the first 60 lines of
  the file are used as context for enrichment.

---

## No real-time graph updates

The graph is a snapshot updated by explicit `ingest_repo` calls (or CI triggers).
It does not reflect unsaved edits, uncommitted changes, or in-flight PRs unless
those commits have been ingested.

---

## Security

- The MCP HTTP server has no built-in authentication. Bind to `127.0.0.1` (default)
  or place a reverse proxy in front before exposing to a network.
- Node IDs and code snippets may contain sensitive information. Treat the graph
  as having the same sensitivity level as your source code.
- The LLM enrichment step sends code snippets to whichever LLM provider is
  configured (Ollama = local/private; OpenAI = cloud). Use Ollama for
  air-gapped or sensitive environments.
