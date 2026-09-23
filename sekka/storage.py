"""Chat history persistence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

PREFIX = "sekka"


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
