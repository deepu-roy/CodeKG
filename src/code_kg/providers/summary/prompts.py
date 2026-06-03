"""Prompt templates for code summarisation."""

from code_kg.domain.models import SummaryRequest

_SYSTEM_PROMPT = """\
You are an expert software engineer analyzing a codebase to build a knowledge graph.
Your task is to produce concise, accurate summaries of code elements that will be stored
as graph node metadata and used for semantic search.

Guidelines:
- Summaries must be factual and grounded in the code/text provided.
- Be precise about what the code does, not just its name.
- Identify the primary purpose, key responsibilities, and important behaviours.
- Keep summaries under 3 sentences (≈ 60 words).
- Tags should be lowercase, singular nouns or short phrases (max 8).
- Complexity: "simple" (< 20 lines, single concern), "complex" (>100 lines, multiple concerns), else "moderate".
"""

_USER_TEMPLATE = """\
Summarise the following {node_type} written in {language}.

Name: {name}
{signature_line}
Code/Content:
```{lang_hint}
{code_or_text}
```
{context_section}
Respond ONLY with valid JSON in this exact shape:
{{
  "summary": "<one to three sentence summary>",
  "tags": ["<tag1>", "<tag2>"],
  "complexity": "simple" | "moderate" | "complex"
}}"""


def build_prompt(request: SummaryRequest) -> tuple[str, str]:
    """Build system + user prompt strings for a SummaryRequest.

    Args:
        request: The summary request.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    language = request.language or "unknown"
    lang_hint = {
        "typescript": "typescript",
        "javascript": "javascript",
        "csharp": "csharp",
        "java": "java",
        "python": "python",
    }.get(language, language)

    signature_line = (
        f"Signature: `{request.node_type}`\n"  # placeholder; callers can inject real sig
    )

    context_section = ""
    if request.context:
        context_section = f"Additional context:\n{request.context}\n\n"

    user = _USER_TEMPLATE.format(
        node_type=request.node_type,
        language=language,
        name=request.name,
        signature_line=signature_line,
        lang_hint=lang_hint,
        code_or_text=request.code_or_text[:4000],  # hard cap to avoid token overflow
        context_section=context_section,
    )
    return _SYSTEM_PROMPT, user
