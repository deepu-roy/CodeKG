# Local Development Setup

Reference for running the full stack locally, running tests, and troubleshooting.

---

## Prerequisites

- Python 3.10+ (3.12 recommended)
- Docker & Docker Compose v2
- Git

---

## Quick start (Docker)

```bash
# 1. Clone and configure
git clone https://github.com/your-org/code-kg.git && cd code-kg
cp .env.example .env
# Edit .env — at minimum set NEO4J__PASSWORD and SUMMARY__MODEL

# 2. Start the stack
docker compose up -d
```

The `ollama-init` service pulls the model specified by `SUMMARY__MODEL` in `.env`
automatically. No manual `ollama pull` needed.

Services started:

| Service | URL | Notes |
|---------|-----|-------|
| Neo4j Browser | http://localhost:7474 | user: `neo4j`, password from `.env` |
| Neo4j Bolt | bolt://localhost:7687 | used by code-kg |
| Ollama API | http://localhost:11434 | |
| code-kg MCP | http://localhost:8765 | MCP server (HTTP transport) |

---

## Choosing and switching LLM models

`SUMMARY__MODEL` in `.env` is the single source of truth:

```dotenv
# .env
SUMMARY__MODEL=qwen2.5-coder:7b    # default (~8 GB, < 16 GB RAM)
# SUMMARY__MODEL=qwen2.5-coder:14b  # higher quality (~16 GB RAM)
```

To switch:
1. Update `SUMMARY__MODEL` in `.env`
2. `docker compose up -d` — `ollama-init` pulls the new model automatically

---

## Volume mounts for ingest

The container can only ingest paths that are volume-mounted. Add mounts in
`docker-compose.yml` under `code-kg-mcp.volumes`:

```yaml
services:
  code-kg-mcp:
    volumes:
      - ./workdir:/app/workdir
      - /absolute/host/path/to/your-repo:/mnt/your-repo   # ← add this
```

Then ingest using the in-container path:

```bash
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/your-repo --pattern "**/*.ts,**/*.cs" --repo your-repo
```

---

## Running commands inside the container

All `code-kg` CLI commands work via `docker compose exec code-kg-mcp`:

```bash
# Bootstrap schema (run once after first start)
docker compose exec code-kg-mcp code-kg bootstrap

# Ingest a repo
docker compose exec code-kg-mcp \
  code-kg ingest /mnt/your-repo --pattern "**/*.ts,**/*.cs" --repo your-repo

# Enrich (--repo is case-insensitive)
docker compose exec code-kg-mcp code-kg enrich --repo your-repo --limit 200

# Map tests
docker compose exec code-kg-mcp code-kg test-map /mnt/your-repo --repo your-repo

# Link docs
docker compose exec code-kg-mcp code-kg link-docs /mnt/your-repo --repo your-repo
```

---

## Local Python (without Docker)

```bash
# Install
pip install -e .
pip install -e ".[dev]"    # includes pytest, ruff, mypy

# Configure
cp .env.example .env.local
# Edit .env.local:
#   NEO4J__URI=bolt://localhost:7687
#   NEO4J__USER=neo4j
#   NEO4J__PASSWORD=password123
#   SUMMARY__MODEL=qwen2.5-coder:7b
#   SUMMARY__BASE_URL=http://localhost:11434

# Bootstrap
code-kg bootstrap
```

When running locally, Ollama must be running on the host (`ollama serve`) and
the model must be pulled manually:

```bash
ollama pull qwen2.5-coder:7b    # or whichever model is in SUMMARY__MODEL
```

---

## Running tests

```bash
# Unit tests — no external services needed
pytest tests/unit/ -v

# Integration tests — requires running Neo4j
pytest tests/integration/ -v

# Eval suite — requires running MCP server on :8765
code-kg eval run --server http://localhost:8765
```

---

## Stack management

```bash
# Stop all services (data preserved)
docker compose down

# Reset everything — deletes all Neo4j data, Ollama models, embeddings
docker compose down -v

# Rebuild just the MCP server (after code changes)
docker compose up -d --build code-kg-mcp

# Tail logs
docker compose logs -f code-kg-mcp
docker compose logs -f ollama
```

---

## Troubleshooting

### Enrich returns "No nodes need enrichment"

Likely a repo slug casing mismatch. The CLI resolves slugs case-insensitively
and prints a note if it corrects the casing:

```
ℹ️  Resolved repo slug 'myrepo' → 'MyRepo'
```

Check what slug the graph actually uses:
```bash
docker compose exec code-kg-mcp python -c "
import asyncio, sys; sys.path.insert(0,'src')
from code_kg.config import Settings
from code_kg.graph.client import Neo4jClient
async def r():
    c = Neo4jClient(Settings().neo4j); await c.connect()
    rows = await c.execute_query('MATCH (r:Repo) RETURN r.name', {})
    print([row['r.name'] for row in rows]); await c.close()
asyncio.run(r())"
```

### Enrichment errors: `404 Not Found` on `/api/chat`

The model configured in `SUMMARY__MODEL` is not loaded in Ollama. Check what
models are available:

```bash
curl http://localhost:11434/api/tags
```

If your model is missing, either update `SUMMARY__MODEL` in `.env` to match what
is available, or restart the stack — `ollama-init` will pull the configured model.

### Ollama URL: Docker vs local Python

| Mode | Ollama address |
|------|---------------|
| Docker Compose | `http://ollama:11434` (internal service name — hardcoded in compose file) |
| Local Python | `http://localhost:11434` (set via `SUMMARY__BASE_URL` in `.env.local`) |

The `docker-compose.yml` always sets `SUMMARY__BASE_URL=http://ollama:11434` for the
`code-kg-mcp` container, overriding whatever is in `.env`. You only need to set
`SUMMARY__BASE_URL` in `.env` / `.env.local` when running outside of Docker.

### Neo4j won't start

```bash
docker compose logs neo4j
lsof -i :7687    # check if port is already in use
```

### MCP server not responding

```bash
docker compose logs code-kg-mcp
curl http://localhost:8765/api/repo-state?slug=test
```

### Migrations fail

Test the Neo4j connection:

```bash
docker compose exec code-kg-mcp python -c \
  "import asyncio, sys; sys.path.insert(0,'src'); \
   from code_kg.config import Settings; from code_kg.graph.client import Neo4jClient; \
   asyncio.run(Neo4jClient(Settings().neo4j).health_check())"
```
