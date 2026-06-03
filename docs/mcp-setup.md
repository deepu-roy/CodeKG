# MCP Setup Guide

This guide shows how to connect CodeKG's MCP server to AI coding assistants
and custom agents. Read tools give your assistant read-only access to the graph;
write tools let it trigger ingestion and enrichment.

---

## Before you start

The MCP server must be running and the graph must be bootstrapped and populated:

```bash
# Start the HTTP server (needed for Copilot, agents, CI)
code-kg mcp http

# Or start the stdio server (needed for Claude Code / Claude Desktop)
code-kg mcp stdio
```

Verify it's responding:
```bash
curl http://localhost:8765/api/repo-state?slug=my-repo
# → {"exists":true, "node_count":843, ...}
```

See [Quick Start](quickstart.md) if you haven't ingested a repo yet.

---

## Claude Code (stdio transport)

Add CodeKG to your Claude Code MCP config. The stdio transport is the most common
and doesn't require a running HTTP server — Claude Code spawns the process itself.

### Global config (`~/.claude/settings.json`)

```json
{
  "mcpServers": {
    "code-kg": {
      "command": "code-kg",
      "args": ["mcp", "stdio"],
      "env": {
        "NEO4J__URI": "bolt://localhost:7687",
        "NEO4J__USER": "neo4j",
        "NEO4J__PASSWORD": "password123"
      }
    }
  }
}
```

### Project-level config (`.claude/settings.json` in your project)

```json
{
  "mcpServers": {
    "code-kg": {
      "command": "code-kg",
      "args": ["mcp", "stdio"]
    }
  }
}
```

This picks up credentials from `.env` / `.env.local` in the project root.

### Verify

In Claude Code, run:
```
/mcp
```
You should see `code-kg` listed with its tools. Then ask:
```
Use the code-kg find_nodes tool to search for "authentication" in my-repo
```

---

## Claude Code (HTTP transport)

If you prefer to run the server as a persistent process (useful when you want
the embedding model loaded once and shared across sessions):

```bash
# In one terminal
code-kg mcp http   # starts on localhost:8765
```

```json
{
  "mcpServers": {
    "code-kg": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

---

## GitHub Copilot (VS Code)

Copilot uses the HTTP transport via the MCP server spec.

1. Start the HTTP server: `code-kg mcp http`

2. Add to your VS Code `settings.json`:

```json
{
  "github.copilot.chat.mcp.servers": {
    "code-kg": {
      "url": "http://localhost:8765/mcp",
      "headers": {}
    }
  }
}
```

3. In Copilot Chat, switch to **Agent mode** and ask:
```
@code-kg find functions in the service layer that handle authentication
```

---

## JetBrains IDEs (AI Assistant)

JetBrains AI Assistant supports MCP via HTTP. Start the server, then add it in
**Settings → Tools → AI Assistant → Model Context Protocol**:

```
URL: http://localhost:8765/mcp
Name: code-kg
```

---

## Custom agent / HTTP client

Call the MCP server directly from any HTTP client using the JSON-RPC 2.0 protocol.

### Initialize a session

```bash
curl -X POST http://localhost:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0", "id": 0, "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "my-agent", "version": "1.0"}
    }
  }'
```

The response includes a `mcp-session-id` header. Pass it in all subsequent requests.

### Call a tool

```bash
SESSION_ID="<from-initialize-response>"

curl -X POST http://localhost:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {
      "name": "find_nodes",
      "arguments": {
        "query": "authentication",
        "repos": ["my-repo"],
        "layers": ["middleware"],
        "limit": 5
      }
    }
  }'
```

### Python example

```python
import httpx, json

base = "http://localhost:8765"

with httpx.Client(timeout=60) as client:
    # Initialize
    resp = client.post(f"{base}/mcp",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05",
                         "capabilities": {}, "clientInfo": {"name": "demo", "version": "1"}}})
    session_id = resp.headers["mcp-session-id"]

    # Call a tool
    resp = client.post(f"{base}/mcp",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "mcp-session-id": session_id},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "impact_analysis",
                         "arguments": {"id": "function:my-repo:...:MyMethod:ab12"}}})

    # Parse SSE or plain JSON response
    body = resp.text
    data = next((l[6:] for l in body.splitlines() if l.startswith("data: ")), body)
    result = json.loads(data)
    print(result["result"]["structuredContent"]["result"])
```

---

## Authentication

The MCP server has **no built-in authentication** by default. Options for securing it:

| Approach | How |
|----------|-----|
| **Bind to localhost only** | Default (`MCP_HTTP_HOST=127.0.0.1`) — blocks external access |
| **Reverse proxy + API key** | Put nginx/Caddy in front; check `Authorization: Bearer <token>` header |
| **VPN / private network** | Expose only within a trusted network |
| **Docker network isolation** | Keep the container on an internal Compose network |

For shared team deployments, the reverse proxy approach is recommended.

---

## Available tools summary

| Tool | Type | What it does |
|------|------|-------------|
| `find_nodes` | read | Hybrid keyword + vector search |
| `get_node` | read | Full node detail with code snippet and neighbours |
| `impact_analysis` | read | Upstream callers, downstream callees, tests, docs |
| `trace_call_chain` | read | Shortest call path between two functions |
| `find_tests_for` | read | Test functions that cover a code node |
| `find_docs_for` | read | Doc sections that reference a code node |
| `find_code_for` | read | Code nodes that a doc section describes |
| `semantic_search` | read | Pure vector similarity search |
| `list_layers` | read | Architectural layers with member counts |
| `get_diff` | read | Graph diff between two commits |
| `ingest_repo` | write | Full or incremental repository ingest |
| `reindex_file` | write | Re-extract and upsert a single file |
| `refresh_summaries` | write | Regenerate LLM summaries and embeddings |
| `run_test_map` | write | Infer TESTS edges |
| `refresh_doc_links` | write | Infer DOCUMENTS edges from Markdown |
| `delete_repo` | write | Hard-delete all nodes for a repo |

Full parameter and response documentation: [MCP Tools Reference](mcp-tools.md).

---

## Troubleshooting

### `No session ID returned from MCP initialize`

The server returned a plain JSON response instead of SSE. Check that `Accept: application/json, text/event-stream` is included in the request header.

### `Connection refused on :8765`

The HTTP server isn't running. Start it with `code-kg mcp http`.

### `Tool returned isError: true`

The Neo4j connection failed or the node ID doesn't exist. Check that:
- Neo4j is running and accessible
- `.env` / `.env.local` has the correct credentials
- You're passing a valid node ID (use `find_nodes` to discover IDs first)

### Tools listed but return empty results

The graph may be empty. Run `code-kg ingest` to populate it, then `code-kg enrich` for semantic search.
