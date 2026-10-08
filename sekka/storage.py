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
SESSION_VERSION = 2
MAX_META_STR = 4096
MAX_META_NOTE = 8_000


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
    overwrite: Optional[Path] = None,
    meta: Optional[dict] = None,
) -> Path:
    """Write ``messages`` to a timestamped file. Returns the path written.

    ``overwrite`` rewrites that exact file (used by autosave so one session
    maps to one file instead of one file per reply).
    """
    directory = Path(directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    saved_at = now or datetime.now()
    path = Path(overwrite) if overwrite else _unique_path(directory, timestamp_name(saved_at, fmt=fmt))

    text = (
        render_markdown(messages, saved_at)
        if fmt == "markdown"
        else session_json(messages, meta=meta, saved_at=saved_at)
    )
    path.write_text(text)
    return path


def session_json(
    messages: Sequence[dict],
    meta: Optional[dict] = None,
    saved_at: Optional[datetime] = None,
) -> str:
    """The JSON text a session file would contain, without writing it.

    Used by /export, which hands the same resumable document to the caller's
    downloads folder (or browser) instead of the save directory.
    """
    saved_at = saved_at or datetime.now()
    payload: dict[str, Any] = {
        "sekka_session": SESSION_VERSION,
        "saved_at": saved_at.isoformat(timespec="seconds"),
        "messages": [
            {"role": m.get("role"), "content": m.get("content")} for m in messages
        ],
    }
    if meta:
        payload["meta"] = dict(meta)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _clean_meta(raw: Any) -> dict:
    """Validate the optional session metadata block (untrusted file content)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StorageError("Session 'meta' must be an object.")
    out: dict = {}
    campaign = raw.get("campaign")
    if isinstance(campaign, str) and campaign.strip() and len(campaign) <= MAX_META_STR:
        out["campaign"] = campaign
    note = raw.get("note")
    if isinstance(note, str) and note.strip() and len(note) <= MAX_META_NOTE:
        out["note"] = note
    labels = raw.get("labels")
    if isinstance(labels, dict):
        clean = {
            k: v
            for k, v in labels.items()
            if k in ("user", "assistant") and isinstance(v, str) and 0 < len(v) <= 30
        }
        if clean:
            out["labels"] = clean
    return out


def load_session(path: str | Path) -> tuple[list[dict], dict]:
    """Return ``(messages, meta)`` from a saved session file."""
    return _load_history(path, with_meta=True)


def load_history(path: str | Path) -> list[dict]:
    """Read and strictly validate a saved sekka JSON session.

    Session files are untrusted input: everything about their shape is
    checked, and only role/content of known-good messages survive - so a
    hand-edited or hostile file can never smuggle unexpected types, roles,
    or absurd sizes into the app. Raises StorageError with a readable
    message on anything unexpected.
    """
    return _load_history(path, with_meta=False)


def _load_history(path: str | Path, with_meta: bool = False):
    """Read and strictly validate a saved sekka JSON session.

    Shared loader; see load_history / load_session.
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
    if with_meta:
        return out, _clean_meta(data.get("meta") if isinstance(data, dict) else None)
    return out


def list_sessions(directory: str | Path) -> list[Path]:
    """All .json files in ``directory``, newest first (by modification time)."""
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return []
    files = [p for p in directory.glob("*.json") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
