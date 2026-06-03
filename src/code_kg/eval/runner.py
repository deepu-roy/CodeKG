"""Evaluation runner for code-kg MCP tools.

Drives the MCP HTTP server against the 10-question eval suite in
``tests/eval/questions.yaml``, records pass/fail + latency per question,
and writes a Markdown report to ``eval-results/<timestamp>.md``.

Usage (CLI):
    code-kg eval run --server http://localhost:8765

Usage (Python):
    from code_kg.eval.runner import run_eval
    asyncio.run(run_eval("http://localhost:8765"))
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

_QUESTIONS_PATH = Path(__file__).parent.parent.parent.parent / "tests" / "eval" / "questions.yaml"
_RESULTS_DIR = Path("eval-results")


# ── MCP HTTP client ───────────────────────────────────────────────────────────


class MCPClient:
    """Minimal async MCP HTTP client — initialize + tool/call."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._session_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=120.0)
        await self._initialize()
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def _initialize(self) -> None:
        resp = await self._client.post(
            f"{self.base_url}/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "eval-runner", "version": "1.0"},
                },
            },
        )
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")
        if not self._session_id:
            raise RuntimeError("No session ID returned from MCP initialize")

    async def call_tool(self, tool: str, arguments: dict, req_id: int = 1) -> Any:
        """Call a tool and return the parsed result (or raise on error)."""
        resp = await self._client.post(
            f"{self.base_url}/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "mcp-session-id": self._session_id,
            },
            json={
                "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        resp.raise_for_status()
        # Parse SSE or plain JSON response
        body = resp.text
        data_line = next(
            (l[6:] for l in body.splitlines() if l.startswith("data: ")),
            body,
        )
        parsed = json.loads(data_line)
        if "error" in parsed:
            raise RuntimeError(f"MCP error: {parsed['error']}")
        result = parsed.get("result", {})
        if result.get("isError"):
            content = result.get("content", [{}])
            raise RuntimeError(f"Tool error: {content[0].get('text', '')[:200]}")

        # Prefer structuredContent.result; fall back to content[0].text
        sc = result.get("structuredContent", {})
        if "result" in sc:
            return sc["result"]
        content = result.get("content", [{}])
        text = content[0].get("text", "") if content else ""
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return text


# ── assertion engine ──────────────────────────────────────────────────────────


def _get_nested(obj: Any, path: str) -> Any:
    """Get a nested value by dotted path, e.g. 'target.name'."""
    for key in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list):
            try:
                obj = obj[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return obj


def evaluate_assertions(result: Any, assertions: list[dict]) -> list[tuple[bool, str]]:
    """Evaluate all assertions against a tool result.

    Returns a list of (passed: bool, message: str) tuples.
    """
    outcomes: list[tuple[bool, str]] = []

    for assertion in assertions:
        atype = assertion["type"]
        msg = assertion.get("message", atype)

        try:
            if atype == "not_null":
                outcomes.append((result is not None, msg))

            elif atype == "is_null":
                outcomes.append((result is None, msg))

            elif atype == "min_results":
                lst = result if isinstance(result, list) else (
                    result.get("nodes", []) if isinstance(result, dict) else []
                )
                outcomes.append((len(lst) >= assertion["value"], msg))

            elif atype == "field_equals":
                val = _get_nested(result, assertion["field"])
                outcomes.append((val == assertion["value"], msg))

            elif atype == "field_present":
                val = _get_nested(result, assertion["field"])
                outcomes.append((val is not None, msg))

            elif atype == "all_have_field":
                lst = result if isinstance(result, list) else (
                    result.get("nodes", []) if isinstance(result, dict) else []
                )
                ok = bool(lst) and all(
                    item.get(assertion["field"]) is not None for item in lst
                )
                outcomes.append((ok, msg))

            elif atype == "all_match_layer":
                lst = result if isinstance(result, list) else (
                    result.get("nodes", []) if isinstance(result, dict) else []
                )
                ok = bool(lst) and all(
                    item.get("layer") == assertion["layer"] for item in lst
                )
                outcomes.append((ok, msg))

            elif atype == "contains_name":
                lst = result if isinstance(result, list) else (
                    result.get("nodes", []) if isinstance(result, dict) else []
                )
                names = {item.get("name") for item in lst}
                outcomes.append((assertion["name"] in names, msg))

            elif atype == "min_neighbors":
                nbrs = result.get("neighbors", []) if isinstance(result, dict) else []
                outcomes.append((len(nbrs) >= assertion["value"], msg))

            elif atype == "min_upstream":
                up = result.get("upstream", []) if isinstance(result, dict) else []
                outcomes.append((len(up) >= assertion["value"], msg))

            elif atype == "upstream_contains_layer":
                up = result.get("upstream", []) if isinstance(result, dict) else []
                layers = {p.get("node", {}).get("layer") for p in up}
                outcomes.append((assertion["layer"] in layers, msg))

            elif atype == "chain_length":
                chain = result.get("chain", []) if isinstance(result, dict) else []
                outcomes.append((len(chain) == assertion["value"], msg))

            elif atype == "chain_contains_name":
                chain = result.get("chain", []) if isinstance(result, dict) else []
                names = {n.get("name") for n in chain}
                outcomes.append((assertion["name"] in names, msg))

            elif atype == "results_have_scores":
                lst = result if isinstance(result, list) else (
                    result.get("nodes", []) if isinstance(result, dict) else []
                )
                ok = bool(lst) and all(
                    (item.get("score") or 0) > 0 for item in lst
                )
                outcomes.append((ok, msg))

            elif atype == "all_type_equals":
                lst = result if isinstance(result, list) else (
                    result.get("nodes", []) if isinstance(result, dict) else []
                )
                ok = bool(lst) and all(
                    item.get("type") == assertion["type_value"] for item in lst
                )
                outcomes.append((ok, msg))

            elif atype == "contains_layer_name":
                lst = result if isinstance(result, list) else []
                names = {item.get("name") for item in lst}
                outcomes.append((assertion["name"] in names, msg))

            elif atype == "all_neighbors_have_edge_type":
                nbrs = result.get("neighbors", []) if isinstance(result, dict) else []
                ok = bool(nbrs) and all(
                    n.get("edge_type") == assertion["edge_type"] for n in nbrs
                )
                outcomes.append((ok, msg))

            else:
                outcomes.append((False, f"Unknown assertion type: {atype}"))

        except Exception as e:
            outcomes.append((False, f"{msg} [assertion error: {e}]"))

    return outcomes


# ── eval result models ────────────────────────────────────────────────────────


@dataclass
class QuestionResult:
    id: str
    title: str
    tool: str
    passed: bool
    latency_ms: float
    assertions_passed: int
    assertions_total: int
    failures: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class EvalReport:
    timestamp: str
    server_url: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    total_latency_ms: float
    avg_latency_ms: float
    results: list[QuestionResult] = field(default_factory=list)


# ── eval runner ───────────────────────────────────────────────────────────────


async def run_eval(
    server_url: str = "http://localhost:8765",
    questions_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    verbose: bool = True,
) -> EvalReport:
    """Run the full evaluation suite against the MCP server.

    Args:
        server_url: Base URL of the MCP HTTP server.
        questions_path: Path to questions YAML (defaults to tests/eval/questions.yaml).
        output_dir: Directory for reports (defaults to eval-results/).
        verbose: Print progress to stdout.

    Returns:
        EvalReport with per-question results and aggregate stats.
    """
    questions_path = questions_path or _QUESTIONS_PATH
    output_dir = output_dir or _RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(questions_path) as f:
        spec = yaml.safe_load(f)
    questions = spec.get("questions", [])

    if verbose:
        print(f"\n{'='*60}")
        print(f"  Code-KG Evaluation Suite")
        print(f"  Server: {server_url}")
        print(f"  Questions: {len(questions)}")
        print(f"{'='*60}\n")

    results: list[QuestionResult] = []

    async with MCPClient(server_url) as client:
        for i, q in enumerate(questions, 1):
            qid = q["id"]
            title = q["title"]
            tool = q["tool"]
            inputs = q.get("input", {})
            assertions = q.get("assertions", [])

            if verbose:
                print(f"[{i:02d}/{len(questions)}] {qid}: {title} ... ", end="", flush=True)

            t0 = time.monotonic()
            error: Optional[str] = None
            raw_result: Any = None

            try:
                raw_result = await client.call_tool(tool, inputs, req_id=i)
            except Exception as e:
                error = str(e)

            latency_ms = round((time.monotonic() - t0) * 1000, 1)

            if error:
                qr = QuestionResult(
                    id=qid, title=title, tool=tool, passed=False,
                    latency_ms=latency_ms, assertions_passed=0,
                    assertions_total=len(assertions),
                    failures=[error], error=error,
                )
                results.append(qr)
                if verbose:
                    print(f"❌  ERROR ({latency_ms}ms)")
                    print(f"        {error[:120]}")
                continue

            # Unwrap find_nodes output (has .nodes list)
            eval_target = raw_result
            if isinstance(raw_result, dict) and "nodes" in raw_result:
                # For list assertions, use .nodes; for field assertions use raw_result
                pass  # assertions handle both shapes

            assertion_results = evaluate_assertions(eval_target, assertions)
            passed_count = sum(1 for ok, _ in assertion_results if ok)
            failed_msgs = [msg for ok, msg in assertion_results if not ok]
            passed = len(failed_msgs) == 0

            qr = QuestionResult(
                id=qid, title=title, tool=tool, passed=passed,
                latency_ms=latency_ms,
                assertions_passed=passed_count,
                assertions_total=len(assertions),
                failures=failed_msgs,
            )
            results.append(qr)

            if verbose:
                icon = "✅" if passed else "❌"
                print(f"{icon}  ({latency_ms}ms, {passed_count}/{len(assertions)} assertions)")
                for msg in failed_msgs:
                    print(f"        ✗ {msg}")

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    total_lat = sum(r.latency_ms for r in results)
    report = EvalReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        server_url=server_url,
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=round(passed / total * 100, 1) if total else 0.0,
        total_latency_ms=round(total_lat, 1),
        avg_latency_ms=round(total_lat / total, 1) if total else 0.0,
        results=results,
    )

    # Write report
    report_path = output_dir / f"eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    _write_report(report, report_path)

    if verbose:
        print(f"\n{'='*60}")
        print(f"  RESULT: {passed}/{total} passed ({report.pass_rate}%)")
        print(f"  Total latency: {report.total_latency_ms}ms  "
              f"Avg: {report.avg_latency_ms}ms/question")
        gate = "✅ PASS (≥70%)" if report.pass_rate >= 70.0 else "❌ FAIL (<70%)"
        print(f"  Gate: {gate}")
        print(f"  Report: {report_path}")
        print(f"{'='*60}\n")

    return report


# ── report writer ─────────────────────────────────────────────────────────────


def _write_report(report: EvalReport, path: Path) -> None:
    gate = "✅ PASS" if report.pass_rate >= 70.0 else "❌ FAIL"
    lines = [
        f"# Code-KG Evaluation Report",
        f"",
        f"**Date:** {report.timestamp}  ",
        f"**Server:** {report.server_url}  ",
        f"**Result:** {gate} — {report.passed}/{report.total} passed ({report.pass_rate}%)",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Questions | {report.total} |",
        f"| Passed | {report.passed} |",
        f"| Failed | {report.failed} |",
        f"| Pass rate | {report.pass_rate}% |",
        f"| Total latency | {report.total_latency_ms}ms |",
        f"| Avg latency / question | {report.avg_latency_ms}ms |",
        f"",
        f"---",
        f"",
        f"## Per-question results",
        f"",
        f"| ID | Title | Tool | Result | Latency | Assertions |",
        f"|----|-------|------|--------|---------|------------|",
    ]

    for r in report.results:
        icon = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.id} | {r.title} | `{r.tool}` | {icon} | {r.latency_ms}ms "
            f"| {r.assertions_passed}/{r.assertions_total} |"
        )

    lines += ["", "---", "", "## Failure details", ""]
    failures_found = False
    for r in report.results:
        if not r.passed:
            failures_found = True
            lines.append(f"### {r.id} — {r.title}")
            if r.error:
                lines.append(f"**Error:** `{r.error}`")
            for msg in r.failures:
                lines.append(f"- ✗ {msg}")
            lines.append("")

    if not failures_found:
        lines.append("_No failures — all questions passed!_")

    path.write_text("\n".join(lines), encoding="utf-8")
