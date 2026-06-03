"""Shared response parsing helpers for summary providers."""

import json
import re

from code_kg.domain.models import SummaryRequest, SummaryResponse


def parse_response(content: str) -> SummaryResponse:
    """Parse JSON from model output, tolerating markdown code fences.

    Args:
        content: Raw model output string.

    Returns:
        Parsed SummaryResponse.

    Raises:
        ValueError: If no JSON object is found.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", content).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output: {content!r}")

    data = json.loads(match.group(0))
    return SummaryResponse(
        summary=data.get("summary", ""),
        tags=data.get("tags", []),
        complexity=data.get("complexity", "moderate"),
    )


def fallback_response(request: SummaryRequest) -> SummaryResponse:
    """Return a safe fallback when the LLM call fails.

    The summary is prefixed with [fallback] so it can be detected and
    cleared by a Cypher query for re-enrichment later.
    """
    return SummaryResponse(
        summary=f"[fallback] {request.node_type.capitalize()} '{request.name}'.",
        tags=[],
        complexity="moderate",
    )
