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
    captured["respond"] = lambda: FakeResponse(200, {"data": [{"id": "a"}, {"id": "b"}, {"nope": 1}]})
    assert list_models("http://host:8000/v1/") == ["a", "b"]
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
        200, None, text="<html>sparkDash</html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    with pytest.raises(ClientError, match="HTML page, not JSON"):
        list_models("http://h/v1")
