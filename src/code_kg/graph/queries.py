"""Parameterised Cypher query templates.

All queries use parameters ($param) — never string interpolation.
"""

# ── Semantic (vector) search ──────────────────────────────────────────────────

SEMANTIC_SEARCH_CODE = """
CALL db.index.vector.queryNodes('code_embeddings', $top_k, $query_vec)
YIELD node, score
WHERE ($repos IS NULL OR node.repo IN $repos)
  AND ($types IS NULL OR any(t IN $types WHERE t IN labels(node)))
  AND ($layers IS NULL OR node.layer IN $layers)
RETURN
  node.id        AS id,
  labels(node)   AS labels,
  node.name      AS name,
  node.repo      AS repo,
  node.filePath  AS filePath,
  node.summary   AS summary,
  node.tags      AS tags,
  node.layer     AS layer,
  score
ORDER BY score DESC
LIMIT $top_k
"""

SEMANTIC_SEARCH_DOCS = """
CALL db.index.vector.queryNodes('doc_embeddings', $top_k, $query_vec)
YIELD node, score
WHERE ($repos IS NULL OR node.repo IN $repos)
RETURN
  node.id        AS id,
  labels(node)   AS labels,
  node.name      AS name,
  node.repo      AS repo,
  node.filePath  AS filePath,
  node.summary   AS summary,
  node.tags      AS tags,
  null           AS layer,
  score
ORDER BY score DESC
LIMIT $top_k
"""

# ── Full-text search ──────────────────────────────────────────────────────────

FULLTEXT_SEARCH_CODE = """
CALL db.index.fulltext.queryNodes('code_search', $query)
YIELD node, score
WHERE ($repos IS NULL OR node.repo IN $repos)
  AND ($types IS NULL OR any(t IN $types WHERE t IN labels(node)))
  AND ($layers IS NULL OR node.layer IN $layers)
RETURN
  node.id        AS id,
  labels(node)   AS labels,
  node.name      AS name,
  node.repo      AS repo,
  node.filePath  AS filePath,
  node.summary   AS summary,
  node.tags      AS tags,
  node.layer     AS layer,
  score
LIMIT $limit
"""

FULLTEXT_SEARCH_DOCS = """
CALL db.index.fulltext.queryNodes('doc_search', $query)
YIELD node, score
WHERE ($repos IS NULL OR node.repo IN $repos)
RETURN
  node.id        AS id,
  labels(node)   AS labels,
  node.name      AS name,
  node.repo      AS repo,
  node.filePath  AS filePath,
  node.summary   AS summary,
  node.tags      AS tags,
  null           AS layer,
  score
LIMIT $limit
"""

# ── Node detail ───────────────────────────────────────────────────────────────

GET_NODE_BY_ID = """
MATCH (n {id: $id})
RETURN
  n.id         AS id,
  labels(n)    AS labels,
  n.name       AS name,
  n.repo       AS repo,
  n.filePath   AS filePath,
  n.summary    AS summary,
  n.tags       AS tags,
  n.layer      AS layer,
  n.signature  AS signature,
  n.lineRange  AS lineRange,
  n.codeSnippet AS codeSnippet
"""

GET_NEIGHBORS = """
MATCH (n {id: $id})-[r]-(m)
WHERE $edge_types IS NULL OR type(r) IN $edge_types
RETURN
  m.id        AS id,
  labels(m)   AS labels,
  m.name      AS name,
  m.repo      AS repo,
  m.filePath  AS filePath,
  m.summary   AS summary,
  m.tags      AS tags,
  m.layer     AS layer,
  type(r)     AS edge_type,
  r.weight    AS weight
LIMIT $limit
"""

# ── Impact analysis ───────────────────────────────────────────────────────────

def IMPACT_UPSTREAM(max_depth: int = 3) -> str:
    """Return the IMPACT_UPSTREAM Cypher query with the depth baked in.

    Neo4j ≤ 5.21 does not support parameter substitution in relationship-length
    specifiers (e.g. ``*1..$max_depth``), so we format the integer in Python.
    """
    return f"""
MATCH path = (caller:CodeNode)-[:CALLS*1..{max_depth}]->(target:CodeNode)
WHERE target.id = $id
  AND caller.deleted_at IS NULL
WITH caller, length(path) AS depth, [x IN nodes(path) | x.id] AS path_ids
RETURN
  caller.id       AS id,
  labels(caller)  AS labels,
  caller.name     AS name,
  caller.repo     AS repo,
  caller.filePath AS filePath,
  caller.summary  AS summary,
  caller.tags     AS tags,
  caller.layer    AS layer,
  depth,
  path_ids
ORDER BY depth, caller.name
LIMIT 100
"""


def IMPACT_DOWNSTREAM(max_depth: int = 3) -> str:
    """Return the IMPACT_DOWNSTREAM Cypher query with the depth baked in."""
    return f"""
MATCH path = (target:CodeNode)-[:CALLS*1..{max_depth}]->(callee:CodeNode)
WHERE target.id = $id
  AND callee.deleted_at IS NULL
WITH callee, length(path) AS depth, [x IN nodes(path) | x.id] AS path_ids
RETURN
  callee.id       AS id,
  labels(callee)  AS labels,
  callee.name     AS name,
  callee.repo     AS repo,
  callee.filePath AS filePath,
  callee.summary  AS summary,
  callee.tags     AS tags,
  callee.layer    AS layer,
  depth,
  path_ids
ORDER BY depth, callee.name
LIMIT 100
"""

IMPACT_TESTS = """
MATCH (test:CodeNode)-[:TESTS]->(target {id: $id})
RETURN
  test.id       AS id,
  labels(test)  AS labels,
  test.name     AS name,
  test.repo     AS repo,
  test.filePath AS filePath,
  test.summary  AS summary,
  test.tags     AS tags,
  test.layer    AS layer,
  1.0           AS score
"""

IMPACT_DOCS = """
MATCH (doc)-[:DOCUMENTS|MENTIONS]->(target {id: $id})
RETURN
  doc.id       AS id,
  labels(doc)  AS labels,
  doc.name     AS name,
  doc.repo     AS repo,
  doc.filePath AS filePath,
  doc.summary  AS summary,
  doc.tags     AS tags,
  null         AS layer,
  1.0          AS score
"""

# ── Call chain ────────────────────────────────────────────────────────────────

def TRACE_CALL_CHAIN(max_depth: int = 6) -> str:
    """Return the TRACE_CALL_CHAIN Cypher query with the depth baked in."""
    return f"""
MATCH path = shortestPath(
  (a:CodeNode)-[:CALLS|INSTANTIATES*1..{max_depth}]->(b:CodeNode)
)
WHERE a.id = $from_id AND b.id = $to_id
RETURN
  [n IN nodes(path) | {{id: n.id, name: n.name, type: labels(n)[0]}}] AS chain,
  length(path) AS depth
"""

# ── Tests ─────────────────────────────────────────────────────────────────────

FIND_TESTS_FOR = """
MATCH (test:CodeNode)-[r:TESTS]->(target {id: $id})
WHERE r.weight >= $min_weight
RETURN
  test.id       AS id,
  labels(test)  AS labels,
  test.name     AS name,
  test.repo     AS repo,
  test.filePath AS filePath,
  test.summary  AS summary,
  test.tags     AS tags,
  test.layer    AS layer,
  r.weight      AS score
ORDER BY r.weight DESC
"""

# ── Diff / changelog ──────────────────────────────────────────────────────────

DIFF_ADDED = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.first_seen_commit = $head_commit
RETURN
  n.id       AS id,
  labels(n)  AS labels,
  n.name     AS name,
  n.repo     AS repo,
  n.filePath AS filePath,
  n.summary  AS summary,
  n.tags     AS tags,
  n.layer    AS layer
"""

DIFF_REMOVED = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.last_seen_commit = $base_commit
  AND n.last_seen_commit <> $head_commit
RETURN
  n.id       AS id,
  labels(n)  AS labels,
  n.name     AS name,
  n.repo     AS repo,
  n.filePath AS filePath,
  n.summary  AS summary,
  n.tags     AS tags,
  n.layer    AS layer
"""

DIFF_MODIFIED = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.first_seen_commit <> $head_commit
  AND n.last_seen_commit = $head_commit
  AND n.fileHash IS NOT NULL
RETURN
  n.id       AS id,
  labels(n)  AS labels,
  n.name     AS name,
  n.repo     AS repo,
  n.filePath AS filePath,
  n.summary  AS summary,
  n.tags     AS tags,
  n.layer    AS layer
"""

# ── Layer queries ─────────────────────────────────────────────────────────────

LIST_LAYERS = """
MATCH (l:Layer {repo: $repo})
RETURN l.name AS name, l.id AS id
ORDER BY l.name
"""

GET_LAYER_MEMBERS = """
MATCH (n:CodeNode)-[:BELONGS_TO_LAYER]->(l:Layer {id: $layer_id})
RETURN
  n.id       AS id,
  labels(n)  AS labels,
  n.name     AS name,
  n.repo     AS repo,
  n.filePath AS filePath,
  n.summary  AS summary,
  n.tags     AS tags,
  n.layer    AS layer
LIMIT $limit
"""

# ── Doc ↔ Code cross-link queries ────────────────────────────────────────────

FIND_DOCS_FOR = """
MATCH (doc)-[r:MENTIONS|DOCUMENTS]->(target)
WHERE target.id = $id
  AND ($min_weight IS NULL OR r.weight >= $min_weight)
RETURN
  doc.id       AS id,
  labels(doc)  AS labels,
  doc.name     AS name,
  doc.repo     AS repo,
  doc.filePath AS filePath,
  doc.summary  AS summary,
  doc.tags     AS tags,
  null         AS layer,
  r.weight     AS score
ORDER BY score DESC
"""

FIND_CODE_FOR = """
MATCH (section)-[r:MENTIONS|DOCUMENTS]->(target:CodeNode)
WHERE section.id = $id
  AND target.deleted_at IS NULL
  AND ($min_weight IS NULL OR r.weight >= $min_weight)
RETURN
  target.id       AS id,
  labels(target)  AS labels,
  target.name     AS name,
  target.repo     AS repo,
  target.filePath AS filePath,
  target.summary  AS summary,
  target.tags     AS tags,
  target.layer    AS layer,
  r.weight        AS score
ORDER BY score DESC
"""

# ── Tests mapper / cleanup helpers ────────────────────────────────────────────

LOAD_TEST_NODES = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.deleted_at IS NULL
  AND (n:Function OR n:Method)
  AND (
    toLower(n.filePath) CONTAINS 'test'
    OR toLower(n.filePath) CONTAINS 'spec'
    OR toLower(n.filePath) CONTAINS '__tests__'
  )
RETURN n.id AS id, n.name AS name, n.filePath AS filePath, labels(n) AS labels
"""

LOAD_ALL_CODE_NODES_FOR_REPO = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.deleted_at IS NULL
RETURN n.id AS id, n.name AS name, n.filePath AS filePath, labels(n) AS labels
"""

LOAD_CALLS_FROM_TEST = """
MATCH (test {id: $test_id})-[:CALLS]->(target:CodeNode {repo: $repo})
WHERE target.deleted_at IS NULL
  AND NOT (
    toLower(target.filePath) CONTAINS 'test'
    OR toLower(target.filePath) CONTAINS 'spec'
  )
RETURN target.id AS id, target.name AS name, target.filePath AS filePath
"""

SOFT_DELETE_FILE_NODES = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.filePath = $file_path AND n.deleted_at IS NULL
SET n.deleted_at = datetime()
RETURN count(n) AS cnt
"""

UPSERT_RENAMED_FROM_EDGE = """
MATCH (old_file:File {repo: $repo}) WHERE old_file.filePath = $old_path
MATCH (new_file:File {repo: $repo}) WHERE new_file.filePath = $new_path
MERGE (old_file)-[r:RENAMED_FROM]->(new_file)
ON CREATE SET r.created_at = datetime(), r.commit_sha = $commit_sha
RETURN r
"""

# ── Docs linker ───────────────────────────────────────────────────────────────

LOAD_SECTIONS_WITH_TEXT = """
MATCH (s:Section {repo: $repo})
WHERE s.deleted_at IS NULL
RETURN s.id AS id, s.name AS name,
       coalesce(s.summary, s.content, s.name) AS text,
       s.summary_embedding AS embedding
ORDER BY s.filePath, s.name
"""

LOAD_ALL_CODE_NAMES_FOR_REPO = """
MATCH (n:CodeNode {repo: $repo})
WHERE n.deleted_at IS NULL
  AND NOT (
    toLower(n.filePath) CONTAINS 'test'
    OR toLower(n.filePath) CONTAINS 'spec'
  )
RETURN n.id AS id, n.name AS name, n.filePath AS filePath
"""
