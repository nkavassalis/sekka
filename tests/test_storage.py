import json
import os
import re
from datetime import datetime

import pytest

from sekka.storage import (
    MAX_CONTENT_CHARS,
    MAX_FILE_BYTES,
    MAX_MESSAGES,
    StorageError,
    list_sessions,
    load_history,
    render_markdown,
    save_history,
    timestamp_name,
)

MESSAGES = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
]
NOW = datetime(2026, 2, 14, 10, 15, 30)


def test_timestamp_name_pattern():
    name = timestamp_name(NOW)
    assert name == "sekka_20260214_101530.json"
    assert timestamp_name(NOW, fmt="markdown").endswith(".md")


def test_save_history_json(tmp_path):
    path = save_history(MESSAGES, directory=tmp_path, now=NOW)
    assert path.name == "sekka_20260214_101530.json"
    payload = json.loads(path.read_text())
    assert payload["messages"] == MESSAGES
    assert payload["saved_at"].startswith("2026-02-14")


def test_save_history_markdown(tmp_path):
    path = save_history(MESSAGES, directory=tmp_path, fmt="markdown", now=NOW)
    assert path.name == "sekka_20260214_101530.md"
    text = path.read_text()
    assert "## User" in text
    assert "hi there" in text


def test_unique_names_on_collision(tmp_path):
    first = save_history(MESSAGES, directory=tmp_path, now=NOW)
    second = save_history(MESSAGES, directory=tmp_path, now=NOW)
    assert first != second
    assert second.name == "sekka_20260214_101530_1.json"
    assert first.is_file() and second.is_file()


def test_creates_missing_directory(tmp_path):
    target = tmp_path / "chats" / "here"
    path = save_history(MESSAGES, directory=target, now=NOW)
    assert path.parent == target


def test_render_markdown_shape():
    text = render_markdown(MESSAGES, NOW)
    assert re.search(r"## User\n\nhello\n", text)
    assert text.startswith("# Sekka chat")


# ------------------------------------------------------------------ resume


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_load_history_roundtrip(tmp_path):
    p = save_history([{"role": "user", "content": "hi"},
                      {"role": "assistant", "content": "yo"}], directory=tmp_path)
    msgs = load_history(p)
    assert msgs == [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "yo"}]


def test_load_history_accepts_bare_list_and_strips_extras(tmp_path):
    p = write(tmp_path / "s.json", json.dumps(
        [{"role": "user", "content": "x", "evil": {"__proto__": 1}, "extra": [1, 2]}]))
    assert load_history(p) == [{"role": "user", "content": "x"}]  # extras dropped


@pytest.mark.parametrize("body,match", [
    ("not json at all", "Not valid JSON"),
    ('{"messages": "hello"}', "must be a list"),
    ('{"messages": [1, 2]}', "not an object"),
    ('{"messages": [{"role": "wizard", "content": "x"}]}', "unsupported role"),
    ('{"messages": [{"role": "user", "content": 42}]}', "non-string content"),
    ('{"messages": [{"role": "tool", "content": "x"}]}', "unsupported role"),
    ('[{"role": "user", "content": null}]', "non-string content"),
    ('42', "must contain a list"),
    ('{"messages": [{"role": "user", "content": "' + "a" * (MAX_CONTENT_CHARS + 1) + '"}]}', "too large"),
])
def test_load_history_rejects_hostile_files(tmp_path, body, match):
    p = write(tmp_path / "bad.json", body)
    with pytest.raises(StorageError, match=match):
        load_history(p)


def test_load_history_rejects_too_many_messages(tmp_path):
    msgs = [{"role": "user", "content": "x"}] * (MAX_MESSAGES + 1)
    p = write(tmp_path / "many.json", json.dumps(msgs))
    with pytest.raises(StorageError, match="Too many"):
        load_history(p)


def test_load_history_rejects_oversized_file(tmp_path):
    p = write(tmp_path / "big.json", "[" + " " * (MAX_FILE_BYTES + 10) + "]")
    with pytest.raises(StorageError, match="too large"):
        load_history(p)


def test_load_history_missing_file(tmp_path):
    with pytest.raises(StorageError, match="No such file"):
        load_history(tmp_path / "gone.json")


def test_list_sessions_sorted_newest_first(tmp_path):
    a = tmp_path / "sekka_a.json"
    b = tmp_path / "sekka_b.json"
    (tmp_path / "notes.txt").write_text("ignore me")
    write(a, "[]")
    write(b, "[]")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    assert list_sessions(tmp_path) == [b, a]


def test_list_sessions_missing_dir():
    assert list_sessions("/nonexistent/dir/here") == []
