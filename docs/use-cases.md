# Use Cases

CodeKG is a structural knowledge graph, not a keyword search engine. It shines
when questions require understanding *relationships* — who calls what, what tests
cover a function, how deep a dependency chain is, which layer a class belongs to.

---

## 1. Complement a semantic index (RAG + graph)

**The problem:** Semantic / vector search retrieves files or functions that are
*textually similar* to a query. It doesn't know that `ClientController.Get` calls
`ClientService.GetClient` which calls `BaseRepository.GetById`, nor that the only
test for this path is `Get_Client_By_Id_Success`.

**How CodeKG helps:**
After vector search finds the entry point (`ClientService.GetClient`), pass its
ID to `impact_analysis` or `trace_call_chain` to retrieve the full execution
context — callers, callees, tests, and related docs — in a single graph traversal.

This produces a richer context block for the LLM than any vector search alone can,
because it captures *structural* relationships that are invisible to embeddings.

**Pattern:**
```
vector search → candidate node IDs → impact_analysis / trace_call_chain → LLM context
```

---

## 2. AI coding assistant (Claude Code, Copilot)

**The problem:** AI assistants generate code in isolation. They can't see that
`EmailService` is already injected as a singleton, or that there's an `IEmailService`
interface that new implementations must satisfy.

**How CodeKG helps:**
With the MCP server connected, the assistant can:
- `find_nodes(query="email service")` → discover existing services
- `get_node(id=...)` → read the full interface contract and existing implementation
- `impact_analysis(id=...)` → see who already depends on this service before suggesting changes
- `find_tests_for(id=...)` → locate tests to update when modifying a function

The assistant makes architecturally aware suggestions rather than generating code
that conflicts with existing patterns.

---

## 3. Software design document (SDD) agent

**The problem:** Writing an SDD for a new feature requires manually tracing how
existing services are composed, which interfaces exist, and how the current
layer boundaries are drawn.

**How CodeKG helps:**
An agent building an SDD can:

1. `list_layers(repo=...)` — understand the current architectural tiers
2. `find_nodes(types=["Interface"], layers=["repository"])` — catalogue all repository contracts
3. `find_nodes(types=["Class"], layers=["service"])` — see what services exist
4. `impact_analysis(id=<affected_service>)` — understand the blast radius of the proposed change
5. `find_docs_for(id=...)` — surface any existing design notes for related code

The agent gets a factual, up-to-date picture of the architecture rather than
relying on potentially stale human-written docs.

---

## 4. Code review automation

**The problem:** Reviewers check whether a PR changes something high-risk
(a widely-called utility, a security guard, a base repository method) but this
judgement is manual and easy to miss.

**How CodeKG helps:**
A review agent can:

1. `get_diff(repo, base_commit, head_commit)` — see what changed in the graph
2. For each modified node, call `impact_analysis(id=...)` — compute how many callers and tests are affected
3. Flag changes with `layer_breakdown.controller > 0` or `upstream count > 5` as high-risk

This surfaces "this function is called by 12 controllers and has only 1 test" in
seconds — the kind of insight that takes a human reviewer much longer to discover.

---

## 5. Onboarding new developers

**The problem:** A developer joining a large codebase spends days tracing "how
does a request get from the HTTP handler to the database?" manually.

**How CodeKG helps:**
- `trace_call_chain(from_id=<controller_action>, to_id=<repository_method>)` — show the exact call path
- `find_nodes(query="candidate", layers=["service"])` — find all candidate-related service classes
- `get_node(id=<class_id>)` — read the LLM-generated summary and the class signature without opening files
- `list_layers(repo=...)` — understand the architecture in one call

A CodeKG-aware assistant can answer "explain how the candidate pipeline works"
with structural accuracy, not just pattern-matched answers from README text.

---

## 6. Architecture visualization and enforcement

**The problem:** Layer violations (a model class calling a controller, a repository
importing a service) accumulate silently over time.

**How CodeKG helps:**
Query for layer violations directly in Cypher:

```cypher
-- Find CALLS edges that cross layer boundaries in the wrong direction
MATCH (a:CodeNode)-[:CALLS]->(b:CodeNode)
WHERE a.layer = 'model' AND b.layer IN ['service', 'controller']
RETURN a.name, b.name, a.filePath
```

Or expose this as a custom check in CI: run a Cypher query after each ingest and
fail the build if violations are found.

---

## 7. Impact analysis before a migration or refactoring

**The problem:** Before renaming `IRepository.GetById` to `IRepository.FindById`,
you need to know every call site across 50 services.

**How CodeKG helps:**
`impact_analysis(id=<GetById_node_id>, max_depth=5)` returns every upstream caller
with depth and layer information. The result includes how many controller, service,
and test nodes would be affected — giving a risk estimate before a single line of
code is changed.

---

## 8. Test coverage discovery

**The problem:** You want to know which functions have zero test coverage in the
graph — no `TESTS` edges pointing at them.

**How CodeKG helps:**
After running `code-kg test-map`, query directly:

```cypher
-- Functions with no TESTS edges
MATCH (f:Function {repo: 'my-repo'})
WHERE NOT ()-[:TESTS]->(f)
  AND f.layer IN ['service', 'repository']
RETURN f.name, f.filePath, f.layer
ORDER BY f.layer
```

This is structural coverage (whether a test *file* targets the code), not
line-level coverage — it complements, not replaces, a coverage tool.

---

## 9. Documentation gap analysis

**The problem:** It's hard to know which service classes or public APIs have no
corresponding documentation.

**How CodeKG helps:**
After running `code-kg link-docs`, query for nodes with no `MENTIONS` or
`DOCUMENTS` edges:

```cypher
-- Classes with no documentation links
MATCH (c:Class {repo: 'my-repo'})
WHERE c.layer IN ['service', 'controller']
  AND NOT ()-[:MENTIONS|DOCUMENTS]->(c)
RETURN c.name, c.layer, c.filePath
```

Feed the results to a doc-generation agent to bootstrap missing documentation.

---

## 10. Multi-repo dependency analysis

**The problem:** A platform team maintains shared libraries consumed by dozens of
services. When a breaking change is made to a shared function, the blast radius
is unknown.

**How CodeKG helps:**
Ingest all services and the shared library into the same Neo4j instance (each
with a different `repo` slug). Then:

```cypher
-- Find all callers of a shared-lib function across all repos
MATCH (caller:Function)-[:CALLS]->(fn:Function {name: 'ParseToken'})
WHERE fn.repo = 'auth-lib'
RETURN caller.repo, caller.name, caller.filePath
ORDER BY caller.repo
```

`impact_analysis` with a cross-repo graph shows how many services depend on the
function, not just how many files in a single repo.

---

## When CodeKG is _not_ the right tool

| Scenario | Better tool |
|----------|-------------|
| Line-level test coverage | Jest/pytest coverage reports, Codecov |
| Runtime profiling / hot paths | OpenTelemetry, Datadog, Jaeger |
| Finding code smells or anti-patterns | SonarQube, ESLint, ReSharper |
| Semantic similarity search only | A vector DB (Pinecone, Weaviate, pgvector) |
| SAST / security scanning | Semgrep, Snyk, Checkmarx |
| PR diff review comments | GitHub code review, Review Board |

CodeKG is most valuable when combined with these tools, not instead of them.
