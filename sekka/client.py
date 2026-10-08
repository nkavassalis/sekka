"""Minimal OpenAI-compatible chat-completions client."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import requests


class ClientError(Exception):
    """Raised for any endpoint/HTTP/parse failure (safe to show in the TUI)."""


@dataclass
class ModelInfo:
    id: str
    max_model_len: Optional[int] = None


@dataclass
class ChatResponse:
    content: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    elapsed: float = 0.0
    reasoning: str = ""  # chain-of-thought if the server exposes one
    finish_reason: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)  # raw assistant message


@dataclass
class StreamEvent:
    """One progress point of a streamed reply. Text fields are cumulative."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    elapsed: float = 0.0
    stopped: bool = False
    finish_reason: str = ""


def _merge_tool_call(acc: dict[int, dict[str, Any]], fragment: dict[str, Any]) -> None:
    """Merge SSE tool-call fragments (keyed by index) into one call."""
    idx = fragment.get("index")
    if not isinstance(idx, int):
        idx = len(acc)
    slot = acc.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    if fragment.get("id"):
        slot["id"] = fragment["id"]
    fn = fragment.get("function") or {}
    if isinstance(fn.get("name"), str):
        slot["function"]["name"] += fn["name"]
    if isinstance(fn.get("arguments"), str):
        slot["function"]["arguments"] += fn["arguments"]


def _stop_watcher(
    stop_event: "threading.Event", finished: "threading.Event", resp: requests.Response
) -> None:
    """Close an in-flight stream from another thread so a stalled read unblocks."""
    while not finished.is_set():
        if stop_event.wait(0.05):
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            return


def stream_chat_completion(
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 120.0,
    tools: Optional[list[dict[str, Any]]] = None,
    reasoning_effort: Optional[str] = None,
    top_p: Optional[float] = None,
    min_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    repetition_penalty: Optional[float] = None,
    stop: Optional[list[str]] = None,
    stop_event: Optional["threading.Event"] = None,
) -> "Iterator[StreamEvent]":
    """POST /chat/completions with stream=true and yield progress events.

    Falls back to a single non-streamed event when the endpoint ignores SSE
    (answers with plain JSON), so servers without streaming still work.
    ``stop_event`` aborts the read: it is polled as chunks arrive and, because
    a stalled endpoint sends nothing to poll on, a watcher also closes the
    connection once it is set. Partial output is kept and flagged ``stopped``.
    """
    url = _base(endpoint) + "/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    # ask for a final usage chunk so the context meter stays exact while streaming
    payload["stream_options"] = {"include_usage": True}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if reasoning_effort and reasoning_effort != "none":
        payload["reasoning_effort"] = reasoning_effort
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    _add_samplers(
        payload,
        top_p=top_p,
        min_p=min_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
        repetition_penalty=repetition_penalty,
        stop=stop,
    )

    def post(body: dict[str, Any]) -> requests.Response:
        try:
            return requests.post(
                url, headers=_headers(api_key), json=body, timeout=_timeout(timeout), stream=True
            )
        except requests.RequestException as exc:
            raise ClientError(f"Could not reach {url}: {exc}") from exc

    resp = post(payload)
    if resp.status_code == 400 and "stream_options" in payload:
        # some servers reject unknown params; retry once without the usage request
        resp.close()
        payload.pop("stream_options")
        resp = post(payload)
    if not resp.ok:
        body = (resp.text or "")[:300].strip()
        resp.close()
        raise ClientError(f"HTTP {resp.status_code} from endpoint: {body}")
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" not in ctype.lower():
        # endpoint ignored stream=true: behave like a plain completion
        with resp:
            data = _check_response(resp)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        usage = data.get("usage") or {}
        yield StreamEvent(
            content=str(msg.get("content") or ""),
            reasoning=str(msg.get("reasoning_content") or msg.get("reasoning") or ""),
            tool_calls=msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else [],
            message=msg,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=str((data.get("choices") or [{}])[0].get("finish_reason") or ""),
        )
        return

    start = time.monotonic()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_acc: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    finish = ""
    stopped = False
    finished = threading.Event()
    if stop_event is not None:
        threading.Thread(
            target=_stop_watcher, args=(stop_event, finished, resp), daemon=True
        ).start()
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if not raw or not raw.startswith("data:"):
                continue
            data_str = raw[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except ValueError as exc:
                raise ClientError(f"Malformed streaming chunk: {exc}") from exc
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if isinstance(choice.get("finish_reason"), str):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content_parts.append(delta["content"])
                piece = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(piece, str):
                    reasoning_parts.append(piece)
                for frag in delta.get("tool_calls") or []:
                    if isinstance(frag, dict):
                        _merge_tool_call(tool_acc, frag)
            yield StreamEvent(
                content="".join(content_parts),
                reasoning="".join(reasoning_parts),
                tool_calls=[tool_acc[k] for k in sorted(tool_acc)],
            )
    except (requests.RequestException, AttributeError, ValueError) as exc:
        # Aborting means closing the response from the watcher thread, which can
        # surface as a ProtocolError or as a low-level "fp is None" inside
        # http.client. With stop_event set that IS the requested outcome.
        if stop_event is None or not stop_event.is_set():
            raise ClientError(f"Stream broken: {exc}") from exc
        stopped = True
    finally:
        finished.set()
        resp.close()
    calls = [tool_acc[k] for k in sorted(tool_acc)]
    text = "".join(content_parts)
    message: dict[str, Any] = {"role": "assistant", "content": text if text or not calls else None}
    if calls:
        message["tool_calls"] = calls
    yield StreamEvent(
        content="".join(content_parts),
        reasoning="".join(reasoning_parts),
        tool_calls=calls,
        message=message,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        elapsed=time.monotonic() - start,
        stopped=stopped,
        finish_reason=finish,
    )


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
    content_type = resp.headers.get("Content-Type", "")
    try:
        return resp.json()
    except ValueError as exc:
        if "html" in content_type.lower():
            raise ClientError(
                "Endpoint returned an HTML page, not JSON - "
                "is the endpoint URL the base API URL of your LLM server "
                "(e.g. http://host:8000/v1), not a web UI?"
            ) from exc
        raise ClientError(f"Endpoint returned invalid JSON: {exc}") from exc


def _timeout(timeout: Optional[float]) -> tuple:
    """(connect, read) tuple; read None means 'wait forever'."""
    return (10.0, float(timeout) if timeout else None)


def list_models(endpoint: str, api_key: str = "", timeout: float = 15.0) -> list[ModelInfo]:
    """GET {endpoint}/models and return ModelInfo entries (id + context size if known)."""
    url = _base(endpoint) + "/models"
    try:
        resp = requests.get(url, headers=_headers(api_key), timeout=_timeout(timeout))
    except requests.RequestException as exc:
        raise ClientError(f"Could not reach {url}: {exc}") from exc
    data = _check_response(resp)
    models = data.get("data", []) if isinstance(data, dict) else []
    infos: list[ModelInfo] = []
    for m in models:
        if isinstance(m, dict) and "id" in m:
            size = m.get("max_model_len")
            infos.append(
                ModelInfo(
                    id=m["id"],
                    max_model_len=size if isinstance(size, int) and size > 0 else None,
                )
            )
    return infos


def _add_samplers(payload: dict[str, Any], **values: Any) -> None:
    """Add optional sampler params, only when the user actually set them.

    min_p and repetition_penalty are vLLM/llama.cpp extensions rather than OpenAI
    fields, so sending them unasked would make strict endpoints reject the whole
    request. Absent means "endpoint default".
    """
    for name, value in values.items():
        if value is None:
            continue
        if name == "stop":
            if value:
                payload["stop"] = list(value)
            continue
        payload[name] = value


def chat_completion(
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 120.0,
    tools: Optional[list[dict[str, Any]]] = None,
    reasoning_effort: Optional[str] = None,
    top_p: Optional[float] = None,
    min_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    repetition_penalty: Optional[float] = None,
    stop: Optional[list[str]] = None,
) -> ChatResponse:
    """POST {endpoint}/chat/completions and return content + usage + timing."""
    url = _base(endpoint) + "/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if reasoning_effort and reasoning_effort != "none":
        payload["reasoning_effort"] = reasoning_effort
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    _add_samplers(
        payload,
        top_p=top_p,
        min_p=min_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
        repetition_penalty=repetition_penalty,
        stop=stop,
    )

    start = time.monotonic()
    try:
        resp = requests.post(
            url, headers=_headers(api_key), json=payload, timeout=_timeout(timeout)
        )
    except requests.RequestException as exc:
        raise ClientError(f"Could not reach {url}: {exc}") from exc
    elapsed = time.monotonic() - start

    data = _check_response(resp)
    finish_reason = ""
    try:
        choices = data.get("choices") or []
        if not choices:
            raise ClientError("Endpoint returned no choices.")
        finish_reason = str(choices[0].get("finish_reason") or "")
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise ClientError("Endpoint returned a non-object message.")
        content = message.get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise ClientError(f"Unexpected response shape: {exc}") from exc

    usage = data.get("usage") or {}
    tool_calls = message.get("tool_calls")
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return ChatResponse(
        content=content if isinstance(content, str) else (str(content) if content else ""),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        elapsed=elapsed,
        reasoning=reasoning if isinstance(reasoning, str) else "",
        tool_calls=tool_calls if isinstance(tool_calls, list) else [],
        message=message,
        finish_reason=finish_reason,
    )
