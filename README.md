# CodeKG — Code Knowledge Graph

Turn any codebase into a queryable knowledge graph. CodeKG uses
[tree-sitter](https://tree-sitter.github.io/tree-sitter/) to parse TypeScript,
JavaScript, C#, and Java into a [Neo4j](https://neo4j.com/) graph, enriches every
node with LLM-generated summaries and semantic embeddings, then exposes the graph
as an [MCP](https://modelcontextprotocol.io/) server your AI assistant can query
in real time.

```
TypeScript / C# / Java / JS
          │
          ▼
    tree-sitter AST
          │  extract classes, methods, imports, calls
          ▼
      Neo4j graph
          │  MERGE nodes & relationships (idempotent)
          ▼
  LLM summaries + embeddings
          │  Ollama (local) or OpenAI (cloud)
          ▼
  MCP server — 16 tools for AI assistants
```

---

## Documentation

| Guide | What it covers |
|-------|---------------|
| [Quick Start](docs/quickstart.md) | Docker or local Python setup, first ingest, first query |
| [Features](docs/features.md) | Full catalogue of what CodeKG extracts and how it works |
| [MCP Setup Guide](docs/mcp-setup.md) | Connect Claude Code, Copilot, or a custom agent to the MCP server |
| [MCP Tools Reference](docs/mcp-tools.md) | All 16 tools with parameters, response shapes, and examples |
| [Configuration Reference](docs/configuration.md) | Every environment variable — Neo4j, embedding models, LLMs, MCP |
| [Known Limitations](docs/limitations.md) | Language support gaps, call resolution limits, performance caveats |
| [Use Cases](docs/use-cases.md) | RAG+graph patterns, SDD agents, code review, onboarding, and more |

---

## 30-second start

```bash
git clone https://github.com/your-org/code-kg.git && cd code-kg
cp .env.example .env
# Edit .env: set SUMMARY__MODEL and add a volume mount for your repo
docker compose up -d          # ollama-init pulls the model automatically
docker compose exec code-kg-mcp code-kg bootstrap
docker compose exec code-kg-mcp code-kg ingest /mnt/your-repo --repo your-repo
docker compose exec code-kg-mcp code-kg enrich --repo your-repo
```

→ Open **http://localhost:7474** and query your graph. Full guide: [Quick Start](docs/quickstart.md).

---

## Prerequisites

| Dependency | Version | Notes |
|-----------|---------|-------|
| Python | 3.10+ | 3.12 recommended |
| Neo4j | 5.11+ | Docker-bundled, or [Aura Free](https://neo4j.com/cloud/aura/) |
| Docker + Compose | v2 | For the recommended setup path |
| Ollama | latest | For local LLM enrichment — [ollama.com](https://ollama.com) |

---

## Installation

### Docker (recommended)

```bash
git clone https://github.com/your-org/code-kg.git
cd code-kg
cp .env.example .env          # set SUMMARY__MODEL and add volume mounts for your repos
docker compose up -d          # ollama-init pulls the configured model automatically
```

Starts: Neo4j on `:7474`/`:7687`, Ollama on `:11434`, MCP server on `:8765`.
All settings are driven by `.env` — no values are hardcoded in `docker-compose.yml`.

### pip (editable)

```bash
pip install -e .
# dev extras: pip install -e ".[dev]"
```

---

## Ingest and query

```bash
# One-time schema setup
code-kg bootstrap

# Ingest a repository
code-kg ingest /path/to/repo --pattern "**/*.ts,**/*.cs" --repo my-service

# Infer test links
code-kg test-map /path/to/repo --repo my-service

# Enrich with summaries and embeddings
code-kg enrich --repo my-service --limit 500

# Start the MCP server (HTTP, for Copilot / custom agents)
code-kg mcp http

# Or stdio (for Claude Code)
code-kg mcp stdio
```

---

## Supported languages

| Language | Status |
|----------|--------|
| TypeScript / JavaScript | ✅ Full |
| C# | ✅ Full |
| Java | ✅ Full |
| Python | 🔜 Planned |
| Go | 🔜 Planned |

---

## Architecture overview

```
src/code_kg/
├── cli.py                        # bootstrap / ingest / enrich / eval commands
├── config.py                     # Pydantic settings (env-driven, nested __)
├── domain/
│   ├── models.py                 # RawNode, NormalizedNode, edges, MCP I/O models
│   └── ids.py                    # stable ID builders (sig-hash for overloads)
├── graph/
│   ├── client.py                 # async Neo4j client (pooling + retry)
│   ├── migrations.py             # schema DDL — constraints, indexes, vector index
│   └── queries.py                # Cypher templates
├── ingestion/
│   ├── tree_sitter_runtime.py    # language parser — TypeScript / C# / Java
│   ├── sources/                  # per-language RawNode/RawEdge extractors
│   ├── normalize.py              # symbol registry and call resolution
│   ├── layers.py                 # architectural layer heuristics
│   ├── upsert.py                 # Neo4j MERGE pipeline
│   ├── enrichment.py             # LLM summary + embedding pipeline
│   ├── tests_mapper.py           # TESTS edge inference
│   └── docs_linker.py            # DOCUMENTS edge inference
├── mcp/
│   ├── server.py                 # FastMCP server — registers all 16 tools
│   ├── tools/read.py             # read tool implementations
│   └── tools/write.py            # write tool implementations
├── providers/
│   ├── embedding/                # sentence-transformers + OpenAI providers
│   └── summary/                  # Ollama + OpenAI LLM providers
└── eval/
    └── runner.py                 # evaluation harness (code-kg eval run)
```

---

## Configuration (key variables)

Full reference: [Configuration Reference](docs/configuration.md).

```dotenv
# Neo4j
NEO4J__URI=bolt://localhost:7687
NEO4J__USER=neo4j
NEO4J__PASSWORD=password123

# Embedding model (local, no API key needed)
EMBEDDING__PROVIDER=sentence_transformers
EMBEDDING__MODEL=BAAI/bge-small-en-v1.5

# Summary LLM (local Ollama)
SUMMARY__PROVIDER=ollama
SUMMARY__MODEL=qwen2.5-coder:7b

# MCP transport
MCP_TRANSPORT=stdio          # or http
MCP_HTTP_PORT=8765
```

---

## Development

```bash
# Unit tests (no Neo4j required)
pytest tests/unit/ -v

# All tests (requires running Neo4j)
pytest tests/ -v

# Linting and type checking
ruff check src/ tests/
mypy src/

# Run the eval suite against a live server
code-kg eval run --server http://localhost:8765
```

### Adding a new language

1. `pip install tree-sitter-<lang>`
2. Register the grammar in `tree_sitter_runtime.py`
3. Implement `_extract_<lang>()` traversal
4. Add `src/code_kg/ingestion/sources/code_<lang>.py`
5. Wire into `cli.py` and add glob patterns to the default `--pattern`
6. Add fixtures under `tests/fixtures/<lang>/` and write unit tests

---

## License

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
