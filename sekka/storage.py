"""Chat history persistence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

PREFIX = "sekka"

# limits for loading (untrusted) session files
MAX_FILE_BYTES = 8_000_000
MAX_MESSAGES = 10_000
MAX_CONTENT_CHARS = 200_000
RESUMABLE_ROLES = {"user", "assistant", "system"}


class StorageError(Exception):
    """A saved file is missing, unreadable, or not a valid sekka session."""


def timestamp_name(
    now: Optional[datetime] = None, fmt: str = "json", prefix: str = PREFIX
) -> str:
    """'sekka_20260214_101530.json' using the moment this is called."""
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    ext = "json" if fmt == "json" else "md"
    return f"{prefix}_{ts}.{ext}"


def _unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, dot, ext = name.partition(".")
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{dot}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def render_markdown(messages: Sequence[dict], saved_at: datetime) -> str:
    lines = [f"# Sekka chat (saved {saved_at.strftime('%Y-%m-%d %H:%M:%S')})", ""]
    for msg in messages:
        role = str(msg.get("role", "unknown")).capitalize()
        lines.append(f"## {role}")
        lines.append("")
        lines.append(str(msg.get("content", "")))
        lines.append("")
    return "\n".join(lines)


def save_history(
    messages: Sequence[dict],
    directory: str | Path = ".",
    fmt: str = "json",
    now: Optional[datetime] = None,
) -> Path:
    """Write ``messages`` to a timestamped file. Returns the path written."""
    directory = Path(directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    saved_at = now or datetime.now()
    path = _unique_path(directory, timestamp_name(saved_at, fmt=fmt))

    if fmt == "markdown":
        path.write_text(render_markdown(messages, saved_at))
    else:
        payload = {
            "saved_at": saved_at.isoformat(timespec="seconds"),
            "messages": [
                {"role": m.get("role"), "content": m.get("content")}
                for m in messages
            ],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_history(path: str | Path) -> list[dict]:
    """Read and strictly validate a saved sekka JSON session.

    Session files are untrusted input: everything about their shape is
    checked, and only role/content of known-good messages survive - so a
    hand-edited or hostile file can never smuggle unexpected types, roles,
    or absurd sizes into the app. Raises StorageError with a readable
    message on anything unexpected.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise StorageError(f"No such file: {path}")
    try:
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            raise StorageError(
                f"Session file is too large ({size} bytes; limit {MAX_FILE_BYTES})."
            )
        data = json.loads(p.read_text(encoding="utf-8"))
    except RecursionError as exc:
        raise StorageError("Session file is nested too deeply.") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise StorageError(f"Could not read file: {exc}") from exc
    except ValueError as exc:
        raise StorageError(f"Not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        messages = data.get("messages")
    elif isinstance(data, list):  # tolerate a bare message list
        messages = data
    else:
        raise StorageError("Session file must contain a list or an object with 'messages'.")

    if not isinstance(messages, list):
        raise StorageError("'messages' must be a list.")
    if len(messages) > MAX_MESSAGES:
        raise StorageError(f"Too many messages ({len(messages)}; limit {MAX_MESSAGES}).")

    out: list[dict] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise StorageError(f"Message {i} is not an object.")
        role = msg.get("role")
        content = msg.get("content")
        if role not in RESUMABLE_ROLES:
            raise StorageError(
                f"Message {i} has unsupported role {role!r} "
                f"(allowed: {sorted(RESUMABLE_ROLES)})."
            )
        if not isinstance(content, str):
            raise StorageError(f"Message {i} has non-string content.")
        if len(content) > MAX_CONTENT_CHARS:
            raise StorageError(
                f"Message {i} content is too large ({len(content)} chars; "
                f"limit {MAX_CONTENT_CHARS})."
            )
        # copy only the two known-good fields; drop anything else
        out.append({"role": role, "content": content})
    return out


def list_sessions(directory: str | Path) -> list[Path]:
    """All .json files in ``directory``, newest first (by modification time)."""
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return []
    files = [p for p in directory.glob("*.json") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
