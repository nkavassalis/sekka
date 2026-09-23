import json
import re
from datetime import datetime

import pytest

from sekka.storage import render_markdown, save_history, timestamp_name

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
