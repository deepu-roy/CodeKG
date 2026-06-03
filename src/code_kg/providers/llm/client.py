"""Shared OpenAI-compatible HTTP chat client.

Both OllamaProvider and OpenAIProvider delegate their HTTP calls here.
This concentrates retry, timeout, and usage-tracking in one place.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300.0   # CPU inference can be slow; 5 min gives headroom
_MAX_RETRIES = 3


class LLMClient:
    """Thin async wrapper around OpenAI-compatible /chat/completions endpoints.

    Compatible with:
    - Ollama  (`POST /api/chat`)
    - OpenAI  (`POST /v1/chat/completions`)
    - Any server that speaks the OpenAI dialect

    Args:
        base_url: Base URL of the API endpoint.
        api_key:  Bearer token (optional; required for OpenAI).
        timeout:  Per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    async def chat(
        self,
        path: str,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        response_format: Optional[dict] = None,
        stream: bool = False,
    ) -> str:
        """Call a chat completions endpoint and return the assistant message content.

        Args:
            path: Endpoint path relative to base_url (e.g. ``/api/chat`` or
                ``/v1/chat/completions``).
            model: Model identifier.
            messages: List of ``{role, content}`` dicts.
            temperature: Sampling temperature.
            response_format: Optional response format dict (e.g. ``{"type": "json_object"}``).
            stream: Whether to request streaming (currently unused; always False).

        Returns:
            The assistant message content string.

        Raises:
            httpx.HTTPStatusError: On 4xx / 5xx responses.
            RuntimeError: If the response shape is unexpected.
        """
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if response_format:
            payload["response_format"] = response_format
            # OpenAI uses top-level temperature, not nested
            payload["temperature"] = temperature
            del payload["options"]

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._client.post(path, json=payload)
                response.raise_for_status()
                data = response.json()

                # Ollama: {"message": {"content": "..."}}
                if "message" in data:
                    return data["message"]["content"]
                # OpenAI: {"choices": [{"message": {"content": "..."}}]}
                if "choices" in data:
                    return data["choices"][0]["message"]["content"]

                raise RuntimeError(f"Unexpected response shape: {list(data.keys())}")

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                # Include the exception class name — some httpx errors (e.g.
                # RemoteProtocolError) have an empty str() which makes the log useless.
                logger.warning(
                    f"LLM request attempt {attempt}/{_MAX_RETRIES} failed: "
                    f"{type(exc).__name__}: {exc or '(no message)'}"
                )

        raise last_exc or RuntimeError("LLM request failed after retries")

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
