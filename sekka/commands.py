"""Slash-command parsing for the chat input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

COMMAND_ALIASES = {
    "help": "help",
    "save": "save",
    "config": "config",
    "clear": "clear",
    "models": "models",
    "knowledge": "knowledge",
    "thinking": "thinking",
    "exit": "exit",
    "quit": "exit",
    "q": "exit",
}

COMMAND_HELP = [
    ("/help", "show this help"),
    ("/save", "save the chat history (asks for confirmation)"),
    ("/config", "open the configuration screen"),
    ("/models", "pick a model from the endpoint"),
    ("/knowledge", "attach knowledge files as tools for the model"),
    ("/thinking", "show/hide model thinking and tool calls (ctrl+t)"),
    ("/clear", "clear the chat history"),
    ("/exit", "quit sekka (same as ctrl+c twice)"),
]


@dataclass
class ParsedInput:
    kind: str  # "text" or "command"
    name: str = ""
    arg: str = ""
    text: str = ""


def parse_input(raw: str) -> ParsedInput:
    """Decide whether ``raw`` is a slash command or a normal chat message."""
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return ParsedInput(kind="text", text=raw)
    parts = stripped[1:].split(None, 1)
    if not parts:
        return ParsedInput(kind="command", name="")
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return ParsedInput(kind="command", name=name, arg=arg)


def resolve_command(name: str) -> Optional[str]:
    """Canonical command name for ``name`` (handles aliases), None if unknown."""
    return COMMAND_ALIASES.get(name)
