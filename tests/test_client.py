import pytest
import requests

from sekka import client
from sekka.client import ClientError, chat_completion, list_models


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def captured(monkeypatch):
    """Patch requests.get/post; returns a dict with 'call' and 'respond' hooks."""
    box = {}

    def fake_get(url, headers=None, timeout=None):
        box["get"] = {"url": url, "headers": headers, "timeout": timeout}
        return box["respond"]()

    def fake_post(url, headers=None, json=None, timeout=None):
        box["post"] = {"url": url, "headers": headers, "json": json, "timeout": timeout}
        return box["respond"]()

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.requests, "post", fake_post)
    box["respond"] = lambda: FakeResponse(200, {"data": []})
    return box


def test_list_models(captured):
    captured["respond"] = lambda: FakeResponse(200, {"data": [
        {"id": "a", "max_model_len": 262144}, {"id": "b"}, {"nope": 1}]})
    models = list_models("http://host:8000/v1/")
    assert [m.id for m in models] == ["a", "b"]
    assert models[0].max_model_len == 262144
    assert models[1].max_model_len is None
    assert captured["get"]["url"] == "http://host:8000/v1/models"


def test_list_models_auth_header(captured):
    list_models("http://h/v1", api_key="secret")
    assert captured["get"]["headers"]["Authorization"] == "Bearer secret"


def test_list_models_http_error(captured):
    captured["respond"] = lambda: FakeResponse(500, text="boom")
    with pytest.raises(ClientError, match="HTTP 500"):
        list_models("http://h/v1")


def test_list_models_connection_error(captured):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    captured["respond"] = boom
    with pytest.raises(ClientError, match="Could not reach"):
        list_models("http://h/v1")


def test_chat_completion_basic(captured):
    captured["respond"] = lambda: FakeResponse(200, {
        "choices": [{"message": {"role": "assistant", "content": "hello!"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
    })
    resp = chat_completion("http://h/v1", "m1", [{"role": "user", "content": "hi"}])
    assert resp.content == "hello!"
    assert resp.completion_tokens == 10
    assert resp.prompt_tokens == 5
    assert resp.elapsed >= 0
    call = captured["post"]
    assert call["url"] == "http://h/v1/chat/completions"
    assert call["timeout"] == (10.0, 120.0)  # (connect, read)
    assert call["json"]["model"] == "m1"
    assert "temperature" not in call["json"]
    assert "max_tokens" not in call["json"]


def test_chat_completion_optional_params(captured):
    captured["respond"] = lambda: FakeResponse(200, {
        "choices": [{"message": {"content": "x"}}],
    })
    chat_completion("http://h/v1", "m", [], temperature=0.2, max_tokens=50)
    payload = captured["post"]["json"]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 50


def test_chat_completion_no_usage(captured):
    captured["respond"] = lambda: FakeResponse(200, {
        "choices": [{"message": {"content": "x"}}],
    })
    resp = chat_completion("http://h/v1", "m", [])
    assert resp.completion_tokens is None


def test_chat_completion_bad_shape(captured):
    captured["respond"] = lambda: FakeResponse(200, {"choices": []})
    with pytest.raises(ClientError, match="no choices"):
        chat_completion("http://h/v1", "m", [])


def test_chat_completion_invalid_json(captured):
    captured["respond"] = lambda: FakeResponse(200, None, text="not json")
    with pytest.raises(ClientError, match="invalid JSON"):
        chat_completion("http://h/v1", "m", [])


def test_html_response_gives_clear_error(captured):
    captured["respond"] = lambda: FakeResponse(
        200, None, text="<html>captive-portal</html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    with pytest.raises(ClientError, match="HTML page, not JSON"):
        list_models("http://h/v1")


def test_timeout_zero_means_wait_forever(captured):
    captured["respond"] = lambda: FakeResponse(200, {"data": []})
    list_models("http://h/v1", timeout=0)
    assert captured["get"]["timeout"] == (10.0, None)


def test_reasoning_and_tools_payload_and_parse(captured):
    captured["respond"] = lambda: FakeResponse(200, {
        "choices": [{"message": {
            "role": "assistant", "content": None,
            "reasoning_content": "I should call the tool",
            "tool_calls": [{"id": "c1", "function": {"name": "read_policy", "arguments": "{}"}}],
        }}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })
    resp = chat_completion(
        "http://h/v1", "m", [{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}], reasoning_effort="low",
    )
    sent = captured["post"]["json"]
    assert sent["reasoning_effort"] == "low"
    assert sent["tools"] == [{"type": "function"}]
    assert sent["tool_choice"] == "auto"
    assert resp.tool_calls[0]["function"]["name"] == "read_policy"
    assert resp.reasoning == "I should call the tool"
    assert resp.content == ""


def test_reasoning_none_is_not_sent(captured):
    captured["respond"] = lambda: FakeResponse(200, {
        "choices": [{"message": {"role": "assistant", "content": "hi"}}]})
    chat_completion("http://h/v1", "m", [], reasoning_effort="none")
    assert "reasoning_effort" not in captured["post"]["json"]
    chat_completion("http://h/v1", "m", [], reasoning_effort="high")
    assert captured["post"]["json"]["reasoning_effort"] == "high"


# --------------------------------------------------------------- streaming SSE
# A real SSE endpoint speaks HTTP/1.1 + Transfer-Encoding: chunked. That detail
# matters: http.server buffers writes, and an HTTP/1.0 body without
# Content-Length makes urllib3 read the whole thing, so a fake server that is
# not chunked cannot prove incremental delivery (both hid a bug first).

import socket
import threading
import json as _json
import time as _time


def _chunk(payload: bytes) -> bytes:
    return f"{len(payload):X}\r\n".encode() + payload + b"\r\n"


class SSEServer:
    """Minimal chunked SSE server. events: list of dicts; '__DONE__' sentinel optional."""

    def __init__(self, events, delay=0.0, stall=0.0, raw_body=None):
        self.events = events
        self.delay = delay
        self.stall = stall
        self.raw_body = raw_body  # if set: reply with plain JSON (ignores stream)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.recv(65536)  # request body (ignored)
        if self.raw_body is not None:
            body = self.raw_body.encode()
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            )
            conn.close()
            return
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        try:
            for ev in self.events:
                conn.sendall(_chunk(b"data: " + _json.dumps(ev).encode() + b"\n\n"))
                if self.delay:
                    _time.sleep(self.delay)
            if self.stall:
                _time.sleep(self.stall)  # endpoint goes quiet mid-reply
            conn.sendall(_chunk(b"data: [DONE]\n\n"))
            conn.sendall(b"0\r\n\r\n")
        except OSError:
            pass  # client aborted
        finally:
            conn.close()

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass


def delta(content=None, reasoning=None, tool_calls=None):
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning_content"] = reasoning
    if tool_calls is not None:
        d["tool_calls"] = tool_calls
    return {"choices": [{"delta": d}]}


def test_stream_yields_tokens_incrementally_and_folds_usage():
    srv = SSEServer(
        [
            delta(reasoning="th"), delta(reasoning="ink"),
            delta("Hel"), delta("lo wor"), delta("ld"), delta(" [OOC]"),
            {"usage": {"prompt_tokens": 11, "completion_tokens": 7}, "choices": []},
        ],
        delay=0.15,
    )
    seen = []
    t0 = _time.monotonic()
    try:
        for ev in client.stream_chat_completion(srv.url, "m", [{"role": "user", "content": "x"}]):
            if ev.content and not ev.message:
                seen.append((_time.monotonic() - t0, ev.content))
        final = ev
    finally:
        srv.stop()
    assert final.content == "Hello world [OOC]"          # concatenated verbatim
    assert final.reasoning == "think"
    assert (final.prompt_tokens, final.completion_tokens) == (11, 7)
    assert final.message["role"] == "assistant" and not final.stopped
    assert [t for t, _ in seen] == sorted(t for t, _ in seen)
    assert seen[0][0] < 0.4, f"first token arrived too late, looks buffered: {seen[0][0]:.2f}s"
    assert seen[0][1] == "Hel"                            # partial text, not the whole reply


def test_stream_merges_tool_call_fragments():
    srv = SSEServer([
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "read_lo"}}]),
        delta(tool_calls=[{"index": 0, "function": {"name": "re"}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"a"'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": ':1}'}}]),
    ])
    try:
        ev = list(client.stream_chat_completion(
            srv.url, "m", [{"role": "user", "content": "x"}]))[-1]
    finally:
        srv.stop()
    assert ev.tool_calls == [{"id": "c1", "type": "function",
                              "function": {"name": "read_lore", "arguments": '{"a":1}'}}]
    assert ev.message["tool_calls"] == ev.tool_calls
    assert ev.message["content"] is None                  # tool call only


def test_stream_stop_keeps_partial_output():
    srv = SSEServer([delta("Hel"), delta("lo wor"), delta("ld")], delay=0.25)
    stop = threading.Event()

    def abort():
        _time.sleep(0.4)  # let ~2 chunks land, then press stop mid-reply
        stop.set()

    threading.Thread(target=abort, daemon=True).start()
    try:
        ev = list(client.stream_chat_completion(
            srv.url, "m", [{"role": "user", "content": "x"}], stop_event=stop))[-1]
    finally:
        srv.stop()
    assert ev.stopped is True
    assert ev.content and len(ev.content) < len("Hello world")


def test_stalled_stream_stops_at_the_ui_not_the_socket():
    """A quiet endpoint has no chunks to poll; the app-level stop is what frees the UI."""
    srv = SSEServer([delta("Hel")], stall=2.0)
    stop = threading.Event()

    def abort():
        _time.sleep(0.3)
        stop.set()

    threading.Thread(target=abort, daemon=True).start()
    try:
        ev = list(client.stream_chat_completion(
            srv.url, "m", [{"role": "user", "content": "x"}], stop_event=stop))[-1]
    finally:
        srv.stop()
    # the client itself only notices the stop when the stream resumes or dies
    assert ev.stopped is True and ev.content == "Hel"


def test_stream_falls_back_when_endpoint_ignores_stream():
    body = _json.dumps({"choices": [{"message": {"role": "assistant", "content": "plain fallback"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    srv = SSEServer([], raw_body=body)
    try:
        events = list(client.stream_chat_completion(srv.url, "m", [{"role": "user", "content": "x"}]))
    finally:
        srv.stop()
    assert len(events) == 1
    assert events[0].content == "plain fallback"
    assert events[0].completion_tokens == 2


def test_stream_asks_for_usage_and_retries_if_the_endpoint_rejects_it():
    """stream_options is optional: a 400 must fall back to a plain stream."""
    from http.server import BaseHTTPRequestHandler as _BH, HTTPServer
    seen = []

    class H(_BH):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = _json.loads(self.rfile.read(length))
            seen.append("stream_options" in body)
            if "stream_options" in body:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"unknown parameter stream_options"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # no usage chunk: without stream_options the server has no reason to send one
            self.wfile.write(
                b"data: " + _json.dumps({"choices": [{"delta": {"content": "hi"}}]}).encode() + b"\n\n"
            )
            self.wfile.write(b"data: [DONE]\n\n")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ev = list(client.stream_chat_completion(
            f"http://127.0.0.1:{srv.server_port}/v1", "m",
            [{"role": "user", "content": "x"}]))[-1]
    finally:
        srv.shutdown()
    assert seen == [True, False], seen          # asked, got rejected, retried plain
    assert ev.content == "hi"
    assert ev.completion_tokens is None          # no usage available without the option


def test_stream_reports_finish_reason():
    srv = SSEServer([delta("hi"), {"choices": [{"delta": {}, "finish_reason": "length"}]}])
    try:
        ev = list(client.stream_chat_completion(
            srv.url, "m", [{"role": "user", "content": "x"}]))[-1]
    finally:
        srv.stop()
    assert ev.finish_reason == "length"
