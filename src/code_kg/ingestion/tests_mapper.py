"""Tests mapper — infers semantic TESTS edges from the code graph.

Three strategies are applied in priority order, with weights reflecting
confidence.  The result is a set of de-duplicated TESTS edges that
``find_tests_for`` can traverse.

Strategies
----------
1. **CALLS graph** (weight 0.9) — if a function in a test file already has a
   CALLS edge to a production function, it very likely tests it.
2. **File-name heuristic** (weight 0.7) — ``CandidateServiceTests.cs`` →
   ``CandidateService.cs``; ``candidate.service.spec.ts`` →
   ``candidate.service.ts``.  Strips common test suffixes/infixes.
3. **Path mirroring** (weight 0.6) — ``tests/Services/CandidateServiceTest.cs``
   mirrors ``src/Services/CandidateService.cs``.

All three strategies contribute to the same TESTS edge — the highest
weight from any matching strategy is kept.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from code_kg.graph.client import Neo4jClient
from code_kg.graph import queries as Q
from code_kg.domain.models import NormalizedEdge
from code_kg.ingestion.upsert import upsert_edge

logger = logging.getLogger(__name__)

# Filename patterns that indicate a test file
_TEST_PATH_RE = re.compile(
    r"(test|spec|tests|__tests__|Test|Tests|Spec)",
    re.IGNORECASE,
)

# Suffixes/infixes stripped to get the base production name
_TEST_SUFFIXES = (
    "Tests", "Test", "Spec", ".spec", ".test",
    "_test", "_tests", "_spec",
)


@dataclass
class TestsMapperResult:
    edges_written: int = 0
    edges_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"TestsMapperResult(written={self.edges_written}, "
            f"skipped={self.edges_skipped}, errors={len(self.errors)})"
        )


# ── helpers ───────────────────────────────────────────────────────────────────


def _is_test_path(file_path: str) -> bool:
    """Return True if *file_path* looks like a test file."""
    return bool(_TEST_PATH_RE.search(file_path))


def _strip_test_suffix(name: str) -> str:
    """Return the base name after stripping common test suffixes.

    Examples::

        "CandidateServiceTests" → "CandidateService"
        "candidate.service.spec" → "candidate.service"
    """
    for suffix in _TEST_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _base_name_from_path(file_path: str) -> str:
    """Return filename stem with test suffix stripped.

    Example: ``tests/Services/CandidateServiceTest.cs`` → ``"CandidateService"``
    """
    stem = Path(file_path).stem
    return _strip_test_suffix(stem)


def _mirror_path(test_path: str) -> Optional[str]:
    """Attempt to mirror a test path to a production path.

    Replaces ``tests/``, ``test/``, ``spec/``, ``__tests__/`` directory
    components with ``src/`` (and strips ``Test``/``Spec`` from the filename).

    Returns None if no mirroring heuristic applies.
    """
    parts = Path(test_path).parts
    test_dir_indices = [
        i for i, p in enumerate(parts)
        if p.lower() in ("test", "tests", "spec", "specs", "__tests__")
    ]
    if not test_dir_indices:
        return None

    idx = test_dir_indices[0]
    new_parts = list(parts)
    new_parts[idx] = "src"

    # Strip test suffix from filename
    stem = Path(parts[-1]).stem
    suffix = Path(parts[-1]).suffix
    new_parts[-1] = _strip_test_suffix(stem) + suffix

    return str(Path(*new_parts))


# ── strategy implementations ──────────────────────────────────────────────────


async def _strategy_calls(
    client: Neo4jClient,
    test_nodes: list[dict],
    repo_slug: str,
) -> dict[tuple[str, str], float]:
    """Strategy 1 — CALLS graph.

    For each test function, query its outgoing CALLS edges to production nodes.
    Returns a ``{(from_id, to_id): weight}`` mapping.
    """
    results: dict[tuple[str, str], float] = {}
    for test in test_nodes:
        try:
            rows = await client.execute_query(
                Q.LOAD_CALLS_FROM_TEST,
                {"test_id": test["id"], "repo": repo_slug},
            )
            for row in rows:
                key = (test["id"], row["id"])
                results[key] = max(results.get(key, 0.0), 0.9)
        except Exception as e:
            logger.debug(f"_strategy_calls failed for {test['id']}: {e}")
    return results


def _strategy_filename(
    test_nodes: list[dict],
    prod_nodes: list[dict],
) -> dict[tuple[str, str], float]:
    """Strategy 2 — file-name heuristic.

    Strips test suffix from test-file stem and looks for production nodes
    whose file stem matches.  Returns ``{(test_id, prod_id): weight}``.
    """
    # Build lookup: base_name → [prod_node_ids]
    prod_by_base: dict[str, list[str]] = {}
    for prod in prod_nodes:
        base = _base_name_from_path(prod["filePath"])
        prod_by_base.setdefault(base.lower(), []).append(prod["id"])

    results: dict[tuple[str, str], float] = {}
    for test in test_nodes:
        base = _base_name_from_path(test["filePath"]).lower()
        for prod_id in prod_by_base.get(base, []):
            key = (test["id"], prod_id)
            results[key] = max(results.get(key, 0.0), 0.7)
    return results


def _strategy_path_mirror(
    test_nodes: list[dict],
    prod_nodes_by_path: dict[str, list[str]],
) -> dict[tuple[str, str], float]:
    """Strategy 3 — path mirroring.

    Mirrors the test file path to a candidate production path and looks up
    nodes by that path.  Returns ``{(test_id, prod_id): weight}``.
    """
    results: dict[tuple[str, str], float] = {}
    for test in test_nodes:
        mirror = _mirror_path(test["filePath"])
        if not mirror:
            continue
        for prod_id in prod_nodes_by_path.get(mirror, []):
            key = (test["id"], prod_id)
            results[key] = max(results.get(key, 0.0), 0.6)
    return results


# ── public entry point ────────────────────────────────────────────────────────


async def map_tests(
    client: Neo4jClient,
    repo_slug: str,
    min_weight: float = 0.5,
) -> TestsMapperResult:
    """Infer and emit TESTS edges for all test nodes in a repository.

    Runs all three strategies and merges their results, keeping the highest
    weight for each (test_node, prod_node) pair.  Writes TESTS edges to Neo4j.

    Args:
        client: Neo4j client.
        repo_slug: Repository slug to scope the run.
        min_weight: Minimum combined weight to emit an edge (default 0.5).

    Returns:
        TestsMapperResult with counts and any non-fatal errors.
    """
    result = TestsMapperResult()

    # ── Load test and production nodes ────────────────────────────────────────
    try:
        test_nodes = await client.execute_query(
            Q.LOAD_TEST_NODES, {"repo": repo_slug}
        )
    except Exception as e:
        result.errors.append(f"Failed to load test nodes: {e}")
        return result

    if not test_nodes:
        logger.info(f"No test nodes found for repo={repo_slug}")
        return result

    try:
        all_nodes = await client.execute_query(
            Q.LOAD_ALL_CODE_NODES_FOR_REPO, {"repo": repo_slug}
        )
    except Exception as e:
        result.errors.append(f"Failed to load production nodes: {e}")
        return result

    prod_nodes = [
        n for n in all_nodes if not _is_test_path(n.get("filePath", ""))
    ]

    # Build path → [node_id] index for path-mirroring strategy
    prod_by_path: dict[str, list[str]] = {}
    for n in prod_nodes:
        prod_by_path.setdefault(n["filePath"], []).append(n["id"])

    logger.info(
        f"tests_mapper: {len(test_nodes)} test nodes, "
        f"{len(prod_nodes)} production nodes for repo={repo_slug}"
    )

    # ── Run strategies ────────────────────────────────────────────────────────
    try:
        calls_edges = await _strategy_calls(client, test_nodes, repo_slug)
    except Exception as e:
        logger.warning(f"Strategy calls failed: {e}")
        calls_edges = {}

    fn_edges = _strategy_filename(test_nodes, prod_nodes)
    pm_edges = _strategy_path_mirror(test_nodes, prod_by_path)

    # Merge: keep the highest weight from any strategy
    all_candidates: dict[tuple[str, str], float] = {}
    for mapping in (calls_edges, fn_edges, pm_edges):
        for (from_id, to_id), weight in mapping.items():
            if from_id != to_id:
                all_candidates[(from_id, to_id)] = max(
                    all_candidates.get((from_id, to_id), 0.0), weight
                )

    # ── Emit TESTS edges ──────────────────────────────────────────────────────
    for (from_id, to_id), weight in all_candidates.items():
        if weight < min_weight:
            result.edges_skipped += 1
            continue
        try:
            edge = NormalizedEdge(
                type="TESTS",
                from_id=from_id,
                to_id=to_id,
                weight=weight,
                unresolved=False,
            )
            ok = await upsert_edge(client, edge, from_id, to_id)
            if ok:
                result.edges_written += 1
            else:
                result.edges_skipped += 1
        except Exception as e:
            logger.warning(f"Failed to write TESTS edge {from_id}→{to_id}: {e}")
            result.errors.append(str(e))

    logger.info(
        f"tests_mapper done: {result.edges_written} written, "
        f"{result.edges_skipped} skipped, {len(result.errors)} errors"
    )
    return result
