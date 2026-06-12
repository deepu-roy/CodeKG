# Quick Start

Get CodeKG running and make your first query in under 10 minutes.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.10+ | 3.12 recommended |
| Docker + Compose v2 | For the recommended path |
| Neo4j 5.11+ | Bundled in Docker; or use [Neo4j Aura Free](https://neo4j.com/cloud/aura/) |
| Ollama | For local LLM summaries — [ollama.com](https://ollama.com) |

---

## Option A — Docker (recommended)

### 1. Clone and configure

```bash
git clone https://github.com/your-org/code-kg.git && cd code-kg
cp .env.example .env          # edit NEO4J__PASSWORD if desired
```

### 2. Mount your repository

The container needs to see your source code. Add a volume mount for every repo
you want to ingest in `docker-compose.yml` under the `code-kg-mcp` service:

```yaml
services:
  code-kg-mcp:
    volumes:
      - ./workdir:/app/workdir
      - /absolute/path/to/your-repo:/mnt/your-repo   # ← add this
```

Use an absolute host path on the left, and any `/mnt/<name>` path on the right.
You can mount as many repos as you like.

> **After editing `docker-compose.yml` you must rebuild the stack:**
> ```bash
> docker compose up -d --build
> ```
> A plain `docker compose up -d` reuses the existing image and will not pick up
> volume or environment changes made to the compose file.

### 3. Choose your LLM model

Open `.env` and set `SUMMARY__MODEL` to whichever model you want. This is the
**single source of truth** — the same value is used by the `ollama-init` service
(which pulls the model on first startup) and by the MCP server during enrichment.

```dotenv
# .env
SUMMARY__MODEL=qwen2.5-coder:7b    # default — fast, ~8 GB download
# SUMMARY__MODEL=qwen2.5-coder:14b  # higher quality, ~16 GB RAM needed
```

You do **not** need to run `ollama pull` manually. The `ollama-init` container
pulls whatever model is in `SUMMARY__MODEL` automatically when the stack starts.
`ollama pull` is idempotent — restarting the stack skips the download if the
model is already present.

### 3b. (Optional) Use Ollama on your host machine

By default the stack runs its own `ollama` container. If you already have Ollama
installed and running on your Mac/Linux host, you can skip the bundled container
and point the MCP server at your host Ollama instead.

Add to your `.env`:

```dotenv
SUMMARY__BASE_URL=http://host.docker.internal:11434
```

Then comment out the `ollama` and `ollama-init` services in `docker-compose.yml`
(or just leave them — they won't be used):

```yaml
  # ollama-init:   # ← comment out
  # ollama:        # ← comment out
```

And remove the `depends_on` entries for those two services from `code-kg-mcp`.
Then rebuild:

```bash
docker compose up -d --build
```

> `host.docker.internal` is the magic hostname Docker provides on macOS and
> Windows to reach the host machine from inside a container. On Linux, use
> `172.17.0.1` (the default Docker bridge gateway) or run
> `ip route | awk '/default/ { print $3 }'` to find your host IP.

### 4. Start the stack

```bash
docker compose up -d
```

This starts four services in order:
1. **neo4j** — graph database on `:7474` / `:7687`
2. **ollama** — LLM runtime on `:11434`
3. **ollama-init** — pulls `$SUMMARY__MODEL` (exits when done)
4. **code-kg-mcp** — MCP server on `:8765` (waits for ollama-init to finish)

On first run, `ollama-init` downloads the model — this takes a few minutes.
Subsequent starts are instant (model is cached in the `ollama-data` volume).

### 5. Bootstrap the schema

```bash
docker compose exec code-kg-mcp code-kg bootstrap
```

### 6. Ingest

Use the `/mnt/<name>` path you configured in step 2:

```bash
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/your-repo \
  --pattern "**/*.ts,**/*.tsx,**/*.cs,**/*.java" \
  --repo your-repo
```

The `--repo` flag sets the namespace for all nodes. The value you choose here
is what you'll pass to `enrich`, `test-map`, etc. later.

**Excluding generated or build files**

CodeKG automatically skips common build directories (`.angular`, `node_modules`,
`dist`, `build`, `bin`, `obj`, `.next`, `__pycache__`, etc.). For anything else,
use `--exclude` with comma-separated glob patterns:

```bash
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/your-repo \
  --pattern "**/*.ts,**/*.tsx,**/*.cs,**/*.java" \
  --exclude "**/generated/**,**/*.g.ts,**/*.designer.cs" \
  --repo your-repo
```

> **Tip:** Generated files (protobuf stubs, EF migrations scaffolding, Angular
> material compiled output) inflate node counts and slow ingestion without adding
> useful knowledge. Excluding them keeps the graph lean and queries fast.

### 7. Enrich with summaries and embeddings

```bash
# Enrich all repos
docker compose exec code-kg-mcp code-kg enrich

# Or limit to a specific repo / batch size
docker compose exec code-kg-mcp code-kg enrich --repo your-repo --limit 200
```

> **Tip:** The `--repo` flag is case-insensitive — `--repo myrepo`, `--repo MyRepo`,
> and `--repo MYREPO` all resolve to the same repo. The CLI will print a note if
> it corrects the casing (e.g. `ℹ️  Resolved repo slug 'myrepo' → 'MyRepo'`).

Enrichment is incremental — only nodes without a summary are processed. Re-run
as many times as needed until all nodes are covered. The HuggingFace model cache
is stored in a named Docker volume (`hf-cache`) so the embedding model is only
downloaded once.

### 8. (Optional) Infer test links and enrich docs

```bash
# Infer TESTS edges (heuristic test-to-code mapping)
docker compose exec code-kg-mcp \
  code-kg test-map /mnt/your-repo --repo your-repo

# Ingest Markdown documentation
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/your-repo --pattern "**/*.md" --repo your-repo
```

---

Open the Neo4j Browser at **http://localhost:7474** (user: `neo4j`, password: `password123`) and run:

```cypher
MATCH (c:Class {layer: 'service'}) RETURN c.name, c.summary LIMIT 20
```

---

## Option B — Local Python

```bash
# 1. Install
git clone https://github.com/your-org/code-kg.git && cd code-kg
pip install -e .

# 2. Configure (minimum required)
cp .env.example .env.local
# Edit .env.local:
#   NEO4J__URI=bolt://localhost:7687
#   NEO4J__USER=neo4j
#   NEO4J__PASSWORD=your-password

# 3. Bootstrap the schema
code-kg bootstrap

# 4. Ingest (add --exclude to skip generated files)
code-kg ingest /path/to/your/repo --pattern "**/*.ts,**/*.cs" --exclude "**/generated/**"

# 5. Enrich (generates summaries + embeddings)
code-kg enrich --repo my-repo
```

---

## Typical full workflow

### Docker

```bash
# One-time setup
docker compose exec code-kg-mcp code-kg bootstrap

# Ingest source code (repo must be volume-mounted — see Option A step 2)
# Use --exclude to skip generated files and keep the graph lean
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/my-service \
  --pattern "**/*.ts,**/*.tsx,**/*.cs,**/*.java" \
  --exclude "**/generated/**,**/*.g.ts,**/*.designer.cs" \
  --repo my-service

# Infer test links (optional but recommended)
docker compose exec code-kg-mcp \
  code-kg test-map /mnt/my-service --repo my-service

# Ingest markdown docs (optional)
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/my-service --pattern "**/*.md" --repo my-service

# Enrich with LLM summaries + embeddings
docker compose exec code-kg-mcp \
  code-kg enrich --repo my-service --limit 500

# MCP server is already running on :8765 — no extra step needed
```

### Local Python

```bash
# One-time setup
code-kg bootstrap

# Ingest source code
code-kg ingest ~/projects/my-service \
  --pattern "**/*.ts,**/*.tsx,**/*.cs,**/*.java" \
  --exclude "**/generated/**,**/*.g.ts,**/*.designer.cs" \
  --repo my-service

# Infer test links (optional but recommended)
code-kg test-map ~/projects/my-service --repo my-service

# Ingest markdown docs (optional)
code-kg ingest ~/projects/my-service --pattern "**/*.md" --repo my-service

# Enrich with LLM summaries + embeddings
code-kg enrich --repo my-service --limit 500

# Start the MCP server for AI tool use
code-kg mcp http
```

---

## Verify the graph

After ingestion, open the Neo4j Browser or run these Cypher queries:

```cypher
-- Node counts by type
MATCH (n) RETURN labels(n) AS type, count(n) AS count ORDER BY count DESC

-- Service-layer classes with summaries
MATCH (c:Class {layer: 'service'}) RETURN c.name, c.summary LIMIT 10

-- Who calls a specific function?
MATCH (caller)-[:CALLS]->(fn:Function {name: 'GetClient'})
RETURN caller.name, labels(caller)
```

---

## Next steps

| I want to… | Go to |
|-----------|-------|
| Connect Claude Code or Copilot | [MCP Setup Guide](mcp-setup.md) |
| See all available MCP tools | [MCP Tools Reference](mcp-tools.md) |
| Understand what CodeKG can do | [Features](features.md) |
| Tune models, Neo4j, embeddings | [Configuration Reference](configuration.md) |
| Know what's not supported yet | [Known Limitations](limitations.md) |
| See real-world use cases | [Use Cases](use-cases.md) |
