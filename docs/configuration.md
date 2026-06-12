# Configuration Reference

All settings are read from environment variables. CodeKG loads `.env` first, then
`.env.local` (which overrides `.env`). Use double-underscore `__` as the nested
key separator.

---

## Neo4j

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J__URI` | *(required)* | Bolt URI — `bolt://localhost:7687` for local, `neo4j+s://xxxx.databases.neo4j.io` for Aura |
| `NEO4J__USER` | *(required)* | Database user |
| `NEO4J__PASSWORD` | *(required)* | Database password |
| `NEO4J__DATABASE` | `neo4j` | Database name. Leave as `neo4j` for Aura Free |

### Choosing a Neo4j tier

| Option | When to use |
|--------|-------------|
| **Local Docker** (`bolt://localhost:7687`) | Development, air-gapped environments |
| **Neo4j Aura Free** (cloud) | Zero-install start; free tier handles repos up to ~2 000 nodes |
| **Aura Professional / Enterprise** | Large repos (50 k+ nodes), production multi-tenant use |

### Minimum Neo4j version

**5.11** — required for the vector index used by semantic search. The fulltext and B-tree indexes work on any 5.x version.

---

## Embedding provider

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING__PROVIDER` | `sentence_transformers` | `sentence_transformers` (local) or `openai` (cloud) |
| `EMBEDDING__MODEL` | `BAAI/bge-small-en-v1.5` | Model name |
| `EMBEDDING__API_KEY` | *(empty)* | Required when `EMBEDDING__PROVIDER=openai` |
| `EMBEDDING__BASE_URL` | *(empty)* | Override for OpenAI-compatible endpoints (e.g. Azure, local proxies) |
| `EMBEDDING__BATCH_SIZE` | `32` | Texts per batch during bulk embedding |

### Supported embedding models

| Model | Provider | Dimensions | Notes |
|-------|----------|-----------|-------|
| `BAAI/bge-small-en-v1.5` | sentence_transformers | 384 | **Default.** Fast, small download (~120 MB), good quality |
| `BAAI/bge-base-en-v1.5` | sentence_transformers | 768 | Better recall, ~400 MB download |
| `BAAI/bge-large-en-v1.5` | sentence_transformers | 1024 | Highest quality local model, ~1.2 GB |
| `text-embedding-3-small` | openai | 1536 | Good balance of cost and quality |
| `text-embedding-3-large` | openai | 3072 | Highest quality, ~2× cost of small |

> **Important:** The vector index dimension is set at `bootstrap` time from `EMBEDDING__DIMENSIONS` (derived from the model). If you change the embedding model, drop and recreate the Neo4j vector index, then re-run `code-kg enrich` for all repos.

---

## Summary / LLM provider

CodeKG supports two provider modes. Both use the same underlying HTTP client — the
difference is the API dialect and authentication behaviour.

| Variable | Default | Description |
|----------|---------|-------------|
| `SUMMARY__PROVIDER` | `ollama` | `ollama` or `openai` — selects the API dialect (see below) |
| `SUMMARY__BASE_URL` | `http://localhost:11434` | Base URL of the LLM server |
| `SUMMARY__MODEL` | `qwen2.5-coder:7b` | Model name passed to the server |
| `SUMMARY__API_KEY` | *(empty)* | Bearer token — required when `SUMMARY__PROVIDER=openai` |
| `SUMMARY__TEMPERATURE` | `0.1` | Lower = more deterministic outputs |
| `SUMMARY__MAX_CONCURRENT` | `1` | Parallel LLM calls during enrichment |

### Provider modes

**`SUMMARY__PROVIDER=ollama`** — uses Ollama's `POST /api/chat` dialect. Assumes no
auth header. Default `BASE_URL` is `http://localhost:11434`.

**`SUMMARY__PROVIDER=openai`** — uses the OpenAI `POST /v1/chat/completions` dialect
with JSON-mode output. Works with any server that speaks this API, including:

- OpenAI (cloud)
- Azure OpenAI
- LM Studio (`http://localhost:1234`)
- llama.cpp server (`http://localhost:8080`)
- vLLM, Mistral AI, Together AI, Groq, and other OpenAI-compatible hosts

For local servers that don't enforce auth, set `SUMMARY__API_KEY` to any non-empty
placeholder string (the field is required but the value is not validated server-side).

### Recommended models

| Model | Where to run | RAM | Notes |
|-------|-------------|-----|-------|
| `qwen2.5-coder:7b` | Ollama | ~8 GB | **Default.** Good quality, works on most machines |
| `qwen2.5-coder:14b` | Ollama | ~16 GB | Higher quality; needs ≥ 16 GB RAM |
| `codellama:13b` | Ollama / llama.cpp | ~16 GB | Alternative local option |
| `deepseek-coder-v2:16b` | Ollama / LM Studio | ~20 GB | Strong alternative |
| `gpt-4o-mini` | OpenAI cloud | — | Fast, cheap, good code comprehension |
| `gpt-4o` | OpenAI cloud | — | Highest quality cloud option |

### Setup examples

**Ollama (default)**
```dotenv
SUMMARY__PROVIDER=ollama
SUMMARY__BASE_URL=http://localhost:11434
SUMMARY__MODEL=qwen2.5-coder:7b
```

**LM Studio**
```dotenv
SUMMARY__PROVIDER=openai
SUMMARY__BASE_URL=http://localhost:1234
SUMMARY__API_KEY=lm-studio
SUMMARY__MODEL=your-model-name
```

**llama.cpp server** (`llama-server --port 8080 -m model.gguf`)
```dotenv
SUMMARY__PROVIDER=openai
SUMMARY__BASE_URL=http://localhost:8080
SUMMARY__API_KEY=no-key-needed
SUMMARY__MODEL=your-model-name
```

**OpenAI cloud**
```dotenv
SUMMARY__PROVIDER=openai
SUMMARY__BASE_URL=https://api.openai.com
SUMMARY__API_KEY=sk-...
SUMMARY__MODEL=gpt-4o-mini
```

**Azure OpenAI**
```dotenv
SUMMARY__PROVIDER=openai
SUMMARY__BASE_URL=https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT
SUMMARY__API_KEY=your-azure-key
SUMMARY__MODEL=gpt-4o
```

### Docker: `.env` is the single source of truth

When running via Docker Compose, all environment variables are read from `.env`.
The `docker-compose.yml` file uses `${VAR:-default}` interpolation — there are no
hardcoded model names in the compose file. To switch the LLM model:

1. Edit `SUMMARY__MODEL` (and optionally `SUMMARY__PROVIDER` / `SUMMARY__BASE_URL`) in `.env`
2. Run `docker compose up -d` — the `ollama-init` service automatically pulls the
   new model before the MCP server starts (only relevant when using Ollama)

> **Important:** If you change `EMBEDDING__MODEL` you must also drop and recreate the
> Neo4j vector index and re-run `code-kg enrich` for all repos (the vector index
> dimension is fixed at bootstrap time). LLM model changes (`SUMMARY__MODEL`) have
> no such constraint.

---

## MCP server

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `stdio` | `stdio` (Claude Code / Claude Desktop) or `http` (Copilot, custom agents) |
| `MCP_HTTP_HOST` | `127.0.0.1` | Bind host for HTTP transport |
| `MCP_HTTP_PORT` | `8765` | Port for HTTP transport |

---

## Workdir (for remote repo ingestion)

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKDIR` | `/tmp/code-kg-repos` | Local directory where remote GitHub repos are cloned |

---

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Full `.env.example`

```dotenv
# ── Neo4j ─────────────────────────────────────────────────────────
NEO4J__URI=bolt://localhost:7687
NEO4J__USER=neo4j
NEO4J__PASSWORD=password123
NEO4J__DATABASE=neo4j

# ── Embeddings ────────────────────────────────────────────────────
EMBEDDING__PROVIDER=sentence_transformers
EMBEDDING__MODEL=BAAI/bge-small-en-v1.5
EMBEDDING__API_KEY=
EMBEDDING__BATCH_SIZE=32

# ── Summaries (LLM) ───────────────────────────────────────────────
SUMMARY__PROVIDER=ollama
SUMMARY__BASE_URL=http://localhost:11434
SUMMARY__MODEL=qwen2.5-coder:7b
SUMMARY__API_KEY=
SUMMARY__TEMPERATURE=0.1
SUMMARY__MAX_CONCURRENT=4

# ── MCP server ────────────────────────────────────────────────────
MCP_TRANSPORT=stdio
MCP_HTTP_HOST=127.0.0.1
MCP_HTTP_PORT=8765

# ── Logging ───────────────────────────────────────────────────────
LOG_LEVEL=INFO

# ── Git workdir (for remote repo ingestion) ───────────────────────
WORKDIR=/tmp/code-kg-repos
```

---

## Performance tuning

### Enrichment throughput

`SUMMARY__MAX_CONCURRENT` controls how many parallel LLM calls run during `code-kg enrich`. Rule of thumb:

- Local CPU inference (Ollama, llama.cpp, LM Studio on CPU): **`1`** — most local servers process one request at a time; higher concurrency causes queued requests to reset, producing spurious retry warnings
- Local GPU inference: `4–8`
- Cloud APIs (OpenAI, Azure, Groq, etc.): `8–16` (watch rate limits)

### Batch embedding

`EMBEDDING__BATCH_SIZE` controls how many texts are embedded in a single model call. Larger batches are more efficient but use more RAM. Default (`32`) is safe for most hardware.

### Large repos

For repos with > 5 000 nodes, run enrichment in incremental batches:

```bash
# Process 500 nodes per run, repeat until done
code-kg enrich --repo my-service --limit 500
```

Each run picks up where the last left off (only unenriched nodes are processed).
