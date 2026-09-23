"""Minimal OpenAI-compatible chat-completions client."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests


class ClientError(Exception):
    """Raised for any endpoint/HTTP/parse failure (safe to show in the TUI)."""


@dataclass
class ChatResponse:
    content: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    elapsed: float = 0.0


def _base(endpoint: str) -> str:
    return endpoint.strip().rstrip("/")


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _check_response(resp: requests.Response) -> Any:
    if resp.status_code != 200:
        detail = (resp.text or "")[:300].strip()
        raise ClientError(f"HTTP {resp.status_code} from endpoint: {detail}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ClientError(f"Endpoint returned invalid JSON: {exc}") from exc


def list_models(endpoint: str, api_key: str = "", timeout: float = 15.0) -> list[str]:
    """GET {endpoint}/models and return the list of model ids."""
    url = _base(endpoint) + "/models"
    try:
        resp = requests.get(url, headers=_headers(api_key), timeout=timeout)
    except requests.RequestException as exc:
        raise ClientError(f"Could not reach {url}: {exc}") from exc
    data = _check_response(resp)
    models = data.get("data", []) if isinstance(data, dict) else []
    return [m["id"] for m in models if isinstance(m, dict) and "id" in m]


def chat_completion(
    endpoint: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 120.0,
) -> ChatResponse:
    """POST {endpoint}/chat/completions and return content + usage + timing."""
    url = _base(endpoint) + "/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    start = time.monotonic()
    try:
        resp = requests.post(
            url, headers=_headers(api_key), json=payload, timeout=timeout
        )
    except requests.RequestException as exc:
        raise ClientError(f"Could not reach {url}: {exc}") from exc
    elapsed = time.monotonic() - start

    data = _check_response(resp)
    try:
        choices = data.get("choices") or []
        if not choices:
            raise ClientError("Endpoint returned no choices.")
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ClientError(f"Unexpected response shape: {exc}") from exc

    usage = data.get("usage") or {}
    return ChatResponse(
        content=content if isinstance(content, str) else str(content),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        elapsed=elapsed,
    )
