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
    "stop": "stop",
    "undo": "undo",
    "edit": "edit",
    "e": "edit",
    "regen": "regen",
    "r": "regen",
    "swipe": "swipe",
    "note": "note",
    "roll": "roll",
    "ooc": "ooc",
    "play": "play",
    "export": "export",
    "rp": "play",
    "state": "note",
    "campaign": "campaign",
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
    ("/stop", "stop the reply being generated; keeps what arrived (ctrl+x)"),
    ("/undo", "delete the last exchange (your message and the reply)"),
    ("/edit", "put your last message back in the input to fix and resend"),
    ("/regen", "replace the last reply with a new one (keeps the old as an alternative)"),
    ("/swipe", "cycle to the other generated versions of the last reply"),
    ("/note", "show, set, +append, or clear the pinned note / running state"),
    ("/roll", "roll real dice, e.g. /roll 2d6+3 (result goes into context)"),
    ("/ooc", "say something out of character, as a single message"),
    ("/play", "roleplaying quick reference (dice, note, takes-backs, keys)"),
    ("/export", "send the transcript to your downloads (browser: a real download)"),
    ("/campaign", "show the loaded campaign, or load one: /campaign FILE.json"),
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


# Shown by /play: grouped so it reads as a cheat sheet, not a man page.
PLAY_WIDTH = 17


PLAY_HELP = [
    ("scenes", [
        ("/roll 2d6+3", "real dice; rides along with your next message"),
        ("/ooc <text>", "out of character, as one message (ctrl+o toggles)"),
        ("/note <text>", "set the pinned running state; /note +x appends"),
    ]),
    ("takes", [
        ("/undo", "delete the last exchange"),
        ("/edit", "your last message, back in the input to fix"),
        ("/regen", "a different reply; the old one is kept"),
        ("/swipe", "cycle the versions of that reply"),
        ("/stop", "stop the reply mid-sentence (ctrl+x)"),
    ]),
    ("world", [
        ("/campaign <file>", "load a campaign (auto-loaded if beside config)"),
        ("/knowledge", "lore files, as tools or keyword triggers"),
        ("/thinking", "show the model's thinking and tool calls (ctrl+t)"),
    ]),
    ("session", [
        ("/save", "save the chat on this machine; /config for autosave"),
        ("/export", "transcript to *your* downloads (browser: a download)"),
        ("/config", "endpoint, model, sampler, prompt, note"),
        ("/models", "pick another model (your pick is remembered)"),
    ]),
]


def play_help_text() -> str:
    """Render PLAY_HELP as the text /play prints."""
    lines = ["Roleplaying quick reference:"]
    for group, rows in PLAY_HELP:
        lines.append("")
        lines.append(f"  {group}")
        for invocation, description in rows:
            lines.append(f"    {invocation:<{PLAY_WIDTH}} {description}")
    lines.append("")
    lines.append("  (full list: /help   -   bindings and labels: /config)")
    return "\n".join(lines)
