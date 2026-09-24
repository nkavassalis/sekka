"""Formatting helpers for response timing / token statistics."""

from __future__ import annotations

from typing import Optional


def tokens_per_second(completion_tokens: Optional[int], elapsed: float) -> Optional[float]:
    """Average generation speed; None when it cannot be computed."""
    if not completion_tokens or elapsed <= 0:
        return None
    return completion_tokens / elapsed


def format_duration(seconds: float) -> str:
    """Human friendly duration, e.g. 1.24s -> '1.2s', 65.0 -> '1m 5.0s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}m {rest:.1f}s"


def format_stats(elapsed: float, completion_tokens: Optional[int]) -> str:
    """Bracketed stats shown after an assistant reply, e.g. '[1.2s, 45.3 tok/s]'."""
    parts = [format_duration(elapsed)]
    tps = tokens_per_second(completion_tokens, elapsed)
    if tps is None:
        parts.append("? tok/s")
    else:
        parts.append(f"{tps:.1f} tok/s")
    return f"[{', '.join(parts)}]"


def format_tokens(n: Optional[int]) -> str:
    """Compact token counts: 262144 -> '262k', 812 -> '812', None -> '?'."""
    if n is None:
        return "?"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def estimate_tokens(text: str) -> int:
    """Cheap character-based token estimate (~4 chars/token)."""
    return max(1, (len(text) + 3) // 4)
