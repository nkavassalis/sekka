"""The Sekka terminal UI (built on Textual).

Layout: chat history takes the top of the screen (config.history_percent,
default 80) and the multi-line input editor the remainder. Arrow keys move
inside the input editor; PageUp/PageDown scroll the history (both
configurable).
"""

from __future__ import annotations

import asyncio
import copy
import io
import os
import threading
import re
import time
from pathlib import Path
from typing import Any, Optional

from rich.color import Color
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DirectoryTree, Input, Label, ListItem, ListView, Select, Static, TextArea

from . import client, commands, dice, storage
from .config import (
    DEFAULT_CONFIG,
    Config,
    deep_merge,
    remember_cli_values,
    save_campaign_values,
    save_config,
    validate_config,
    ConfigError,
)
from .stats import estimate_tokens, format_stats, format_tokens


def _hex(color: str) -> str:
    """Convert any rich-recognised color name to a hex string for Textual CSS."""
    return Color.parse(color).get_truecolor().hex


# Textual's own `textual-dark` is built for a *terminal palette*: text sits at 87%
# luminance behind a 0.95 alpha, foreground is #E0E0E0, and the panel borders use
# primary #0178D4 (luminance ~101). On a real terminal you never see those values -
# the emulator substitutes its own palette for the ANSI slots and most palettes
# brighten them. In a browser (textual-serve runs the app in truecolor) they are
# painted literally, which is the whole "sekka looks faint in the browser" effect.
# So sekka ships a theme of its own: white text, no alpha loss, bright borders.
SEKKA_THEME = Theme(
    name="sekka",
    primary="#00afff",        # borders + focus: was #0178D4
    secondary="#5f87d7",      # file-picker / knowledge borders: was #004578
    background="#121212",     # stay terminal-dark; brightness comes from the ink
    surface="#1e1e1e",
    panel="#24343c",
    foreground="#ffffff",     # was #E0E0E0
    success="#5fd787",
    warning="#ffd75f",
    error="#ff5f5f",
    boost="#FFFFFF14",
    dark=True,
    text_alpha=1.0,           # textual-dark dims all text to 95%; not needed here
    variables={"text": "auto 100%", "text-muted": "auto 72%"},   # was auto 87% / 60%
)


class Submit(Message):
    """Posted by the input editor when the user presses the submit key."""


class ChatInput(TextArea):
    """TextArea with Sekka's configurable key routing."""

    def __init__(self, keymap: dict[str, str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.keymap = dict(keymap)

    def set_keymap(self, keymap: dict[str, str]) -> None:
        self.keymap = dict(keymap)

    async def _on_key(self, event) -> None:
        key = event.key
        if key == self.keymap.get("submit"):
            event.stop()
            event.prevent_default()
            self.post_message(Submit())
            return
        if key == self.keymap.get("newline"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == self.keymap.get("scroll_up"):
            event.stop()
            event.prevent_default()
            self.app.action_history_scroll_up()
            return
        if key == self.keymap.get("scroll_down"):
            event.stop()
            event.prevent_default()
            self.app.action_history_scroll_down()
            return
        await super()._on_key(event)


class ModelScreen(ModalScreen[Optional[str]]):
    """Pick a model from the list reported by the endpoint."""

    DEFAULT_CSS = """
    ModelScreen { align: center middle; }
    ModelScreen > Vertical {
        width: 60%; max-width: 90; min-height: 6; height: auto; max-height: 80%;
        padding: 1 2; background: $surface; border: thick $primary;
    }
    ListView { height: auto; max-height: 100%; }
    """

    def __init__(self, models: list[str]) -> None:
        super().__init__()
        self.models = models

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Select a model (enter to choose, esc to cancel):", classes="msg-system")
            yield ListView(
                *[ListItem(Label(name, id=f"model-item-{i}", markup=False)) for i, name in enumerate(self.models)],
                id="model_list",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        label = event.item.children[0] if event.item.children else None
        if label is not None and label.id and label.id.startswith("model-item-"):
            self.dismiss(self.models[int(label.id[len("model-item-"):])])
        else:
            self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 60%; max-width: 80; height: auto;
        padding: 1 2; background: $surface; border: thick $primary;
    }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.question, markup=False)
            with Vertical(id="confirm_buttons"):
                yield Button("Yes", id="yes", variant="primary")
                yield Button("No", id="no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfigScreen(ModalScreen[Optional[dict]]):
    """The /config screen: edit endpoint, model, system prompt, etc."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("pageup", "form_up", "Scroll form up", priority=True),
        Binding("pagedown", "form_down", "Scroll form down", priority=True),
    ]

    DEFAULT_CSS = """
    ConfigScreen { align: center middle; }
    ConfigScreen > Vertical {
        width: 80%; max-width: 100; height: 92%;
        padding: 0 0 1 2; background: $surface; border: thick $primary;
    }
    #cfg_scroll { width: 1fr; height: 1fr; }
    ConfigScreen Label { padding-top: 1; color: $primary; }
    #config_labels Input { width: 1fr; }
    #config_samplers { height: auto; }
    #config_samplers Input { width: 1fr; }
    #config_labels { height: auto; }
    ConfigScreen TextArea { height: 6; border: round $primary 40%; }
    ConfigScreen Input { border: round $primary 40%; }
    #config_buttons { align-horizontal: right; height: auto; }
    #config_buttons Button { margin-left: 1; }
    """

    def action_cancel(self) -> None:
        self.dismiss(None)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        values = self.config.values
        with Vertical():
            with VerticalScroll(id="cfg_scroll"):
                yield Static("Sekka configuration (PageUp/PageDown to scroll, esc cancels)", classes="msg-system")
                yield Label("Endpoint")
                yield Input(value=str(values.get("endpoint", "")), id="cfg_endpoint")
                yield Label("Model (leave empty to pick from endpoint)")
                yield Input(value=str(values.get("model", "")), id="cfg_model")
                yield Label("Your name label / Assistant name label")
                with Vertical(id="config_labels"):
                    yield Input(value=str(values.get("labels", {}).get("user", "You")), id="cfg_label_user", placeholder="You")
                    yield Input(value=str(values.get("labels", {}).get("assistant", "Assistant")), id="cfg_label_assistant", placeholder="Assistant")
                yield Label("API key (optional)")
                yield Input(value=str(values.get("api_key", "")), password=True, id="cfg_api_key")
                yield Label("System prompt")
                yield TextArea(str(values.get("system_prompt", "")), id="cfg_system")
                yield Label("Pinned note / running state (sent with every request; /note edits it too)")
                yield TextArea(str(values.get("note", "")), id="cfg_note")
                yield Label("Temperature (blank = endpoint default)")
                yield Input(value=_blank_if_none(values.get("temperature")), id="cfg_temperature")
                yield Label("Max tokens (blank = endpoint default)")
                yield Input(value=_blank_if_none(values.get("max_tokens")), id="cfg_max_tokens")
                yield Label("Sampler tweaks (blank = endpoint default; min_p / repetition_penalty need vLLM or llama.cpp)")
                with Vertical(id="config_samplers"):
                    yield Input(placeholder="top_p", value=_blank_if_none(values.get("top_p")), id="cfg_top_p")
                    yield Input(placeholder="min_p", value=_blank_if_none(values.get("min_p")), id="cfg_min_p")
                    yield Input(placeholder="presence_penalty", value=_blank_if_none(values.get("presence_penalty")), id="cfg_presence_penalty")
                    yield Input(placeholder="frequency_penalty", value=_blank_if_none(values.get("frequency_penalty")), id="cfg_frequency_penalty")
                    yield Input(placeholder="repetition_penalty", value=_blank_if_none(values.get("repetition_penalty")), id="cfg_repetition_penalty")
                    yield Input(placeholder="stop sequences, comma separated", value=", ".join(values.get("stop") or []), id="cfg_stop")
                yield Label("Context window tokens (blank = from endpoint)")
                yield Input(value=_blank_if_none(values.get("context_window")), id="cfg_context_window")
                yield Label("When the context window fills")
                yield Select(
                    [
                        ("Pause - block new messages", "pause"),
                        ("Rolling - drop oldest messages", "rolling"),
                        ("Compact - summarize old messages", "compact"),
                    ],
                    value=values.get("context_mode", "pause"),
                    allow_blank=False,
                    id="cfg_context_mode",
                )
                yield Label("Request timeout seconds (0 = wait forever)")
                yield Input(value=_blank_if_none(values.get("request_timeout")), id="cfg_timeout")
                yield Label("Reasoning effort (none = don't send it)")
                yield Select(
                    [
                        ("none - don't send (models without thinking)", "none"),
                        ("minimal", "minimal"),
                        ("low", "low"),
                        ("medium", "medium"),
                        ("high", "high"),
                    ],
                    value=values.get("reasoning", "medium"),
                    allow_blank=False,
                    id="cfg_reasoning",
                )
                yield Label("History height % (50-95)")
                yield Input(value=_blank_if_none(values.get("history_percent")), id="cfg_history_percent")
                yield Label("Save directory")
                yield Input(value=str(values.get("save_dir", ".")), id="cfg_save_dir")
                yield Label("Save format")
                yield Select(
                    [("JSON", "json"), ("Markdown", "markdown")],
                    value=values.get("save_format", "json"),
                    allow_blank=False,
                    id="cfg_save_format",
                )
                yield Checkbox("Stream replies token by token", value=bool(values.get("stream", True)), id="cfg_stream")
                yield Checkbox("Autosave history after every reply", value=bool(values.get("autosave")), id="cfg_autosave")
            with Vertical(id="config_buttons"):
                yield Button("Save", id="config_save", variant="primary")
                yield Button("Cancel", id="config_cancel", variant="default")

    def action_form_up(self) -> None:
        self.query_one("#cfg_scroll", VerticalScroll).scroll_page_up(animate=False)

    def action_form_down(self) -> None:
        self.query_one("#cfg_scroll", VerticalScroll).scroll_page_down(animate=False)

    def on_descendant_focus(self, event) -> None:
        """Keep the focused field visible when tabbing through a short screen."""
        widget = self.app.focused
        if widget is not None:
            self.query_one("#cfg_scroll", VerticalScroll).scroll_to_widget(
                widget, top=True, immediate=True
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config_cancel":
            self.action_cancel()
            return

        endpoint = self.query_one("#cfg_endpoint", Input).value.strip()
        if not endpoint:
            self.notify("Endpoint must not be empty.", severity="error")
            return

        def _num(widget_id: str, cast):
            raw = self.query_one(f"#{widget_id}", Input).value.strip()
            if not raw:
                return None
            try:
                return cast(raw)
            except ValueError:
                self.notify(f"Invalid number: {raw!r}", severity="error")
                raise _ConfigInputError

        try:
            temperature = _num("cfg_temperature", float)
            top_p = _num("cfg_top_p", float)
            min_p = _num("cfg_min_p", float)
            presence_penalty = _num("cfg_presence_penalty", float)
            frequency_penalty = _num("cfg_frequency_penalty", float)
            repetition_penalty = _num("cfg_repetition_penalty", float)
            max_tokens = _num("cfg_max_tokens", int)
            history_percent = _num("cfg_history_percent", int)
            if history_percent is not None and not 50 <= history_percent <= 95:
                self.notify("History height % must be between 50 and 95.", severity="error")
                return
            context_window = _num("cfg_context_window", int)
            if context_window is not None and context_window < 1024:
                self.notify("Context window must be at least 1024 tokens.", severity="error")
                return
            timeout = _num("cfg_timeout", float)
            if timeout is not None and timeout < 0:
                self.notify("Timeout must be 0 (wait forever) or positive.", severity="error")
                return
        except _ConfigInputError:
            return

        user_label = self.query_one("#cfg_label_user", Input).value.strip() or "You"
        assistant_label = self.query_one("#cfg_label_assistant", Input).value.strip() or "Assistant"

        self.dismiss(
            {
                "endpoint": endpoint,
                "model": self.query_one("#cfg_model", Input).value.strip(),
                "labels": {"user": user_label, "assistant": assistant_label},
                "api_key": self.query_one("#cfg_api_key", Input).value,
                "system_prompt": self.query_one("#cfg_system", TextArea).text,
                "note": self.query_one("#cfg_note", TextArea).text,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "top_p": top_p,
                "min_p": min_p,
                "presence_penalty": presence_penalty,
                "frequency_penalty": frequency_penalty,
                "repetition_penalty": repetition_penalty,
                "stop": [
                    part.strip()
                    for part in self.query_one("#cfg_stop", Input).value.split(",")
                    if part.strip()
                ],
                "history_percent": history_percent if history_percent is not None else self.config.get("history_percent", 80),
                "context_window": context_window,
                "context_mode": str(self.query_one("#cfg_context_mode", Select).value),
                "request_timeout": timeout if timeout is not None else self.config.get("request_timeout", 300),
                "reasoning": str(self.query_one("#cfg_reasoning", Select).value),
                "save_dir": self.query_one("#cfg_save_dir", Input).value.strip() or ".",
                "save_format": str(self.query_one("#cfg_save_format", Select).value),
                "stream": bool(self.query_one("#cfg_stream", Checkbox).value),
                "autosave": bool(self.query_one("#cfg_autosave", Checkbox).value),
            }
        )


class _ConfigInputError(Exception):
    pass


class ResumeScreen(ModalScreen[Optional[str]]):
    """Pick a saved session file (from the configured save directory)."""

    DEFAULT_CSS = """
    ResumeScreen { align: center middle; }
    ResumeScreen > Vertical {
        width: 70%; max-width: 100; min-height: 6; max-height: 80%;
        padding: 1 2; background: $surface; border: thick $primary;
    }
    ListView { height: auto; max-height: 100%; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, paths: list[Path], directory: Path) -> None:
        super().__init__()
        self.paths = paths
        self.directory = directory

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Resume a session from {self.directory} (enter to choose, esc to cancel):",
                classes="msg-system",
            )
            yield ListView(
                *[
                    ListItem(Label(f"{p.name}  ({p.stat().st_size // 1024} KB)", id=f"session-item-{i}", markup=False))
                    for i, p in enumerate(self.paths)
                ],
                id="session_list",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        label = event.item.children[0] if event.item.children else None
        if label is not None and label.id and label.id.startswith("session-item-"):
            self.dismiss(str(self.paths[int(label.id[len("session-item-"):])]))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class FileBrowseScreen(ModalScreen[Optional[str]]):
    """Minimal file browser; starts in the project's .sekka dir when present."""

    DEFAULT_CSS = """
    FileBrowseScreen { align: center middle; }
    FileBrowseScreen > Vertical {
        width: 70%; max-width: 100; height: 80%;
        padding: 1 2; background: $surface; border: thick $primary;
    }
    DirectoryTree { height: 1fr; border: round $secondary; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, start_path: str) -> None:
        super().__init__()
        self.start_path = start_path

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Pick a file (enter), browse dirs, esc to cancel:", classes="msg-system")
            yield DirectoryTree(self.start_path)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(event.path))

    def action_cancel(self) -> None:
        self.dismiss(None)


class KnowledgeScreen(ModalScreen[None]):
    """Manage knowledge files; each enabled entry becomes a read-only tool.

    Security model: the model may only ever read the files listed here, and
    only via the fixed tool names we generate - the tool takes no arguments,
    so the model cannot name a file to open. sekka itself does the reading.
    """

    DEFAULT_CSS = """
    KnowledgeScreen { align: center middle; }
    KnowledgeScreen > Vertical {
        width: 90%; max-width: 110; height: 86%;
        padding: 1 2; background: $surface; border: thick $primary;
    }
    #k_entries { height: auto; max-height: 1fr; min-height: 3; border: round $secondary; }
    .k_entry { height: auto; }
    .k_entry Checkbox { width: auto; max-width: 55%; }
    .k_desc { width: 1fr; color: $sekka-stats; }
    #k_form_row { height: auto; }
    #k_path { width: 1fr; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "Knowledge files - each ENABLED entry is offered to the model as a "
                "read-only tool (requires a tool-calling model). Saved to the config "
                "file immediately; disabled entries are remembered but not offered.",
                classes="msg-system",
            )
            yield VerticalScroll(id="k_entries")
            yield Label("File path (name turns green when the file exists)")
            with Horizontal(id="k_form_row"):
                yield Input(placeholder=".sekka/credit_card_processing.md", id="k_path")
                yield Button("browse", id="k_browse", variant="default")
            yield Label("Description - what is in it and when to use it (this is what convinces the model to call it)")
            yield Input(placeholder="Full policy for credit card processing at the clinic", id="k_desc")
            yield Label("Trigger keywords - the file is loaded automatically when one appears in your message (comma separated, optional)")
            yield Input(placeholder="tavern, seraine, cold art", id="k_keywords")
            yield Checkbox("Always in context (skip the lookup entirely)", value=False, id="k_always")
            with Horizontal(id="k_close_row"):
                yield Button("add", id="k_add", variant="primary")
                yield Button("close", id="k_close", variant="default")

    def on_show(self) -> None:
        self._refresh_list()

    @staticmethod
    def _entry_summary(entry: dict[str, Any]) -> str:
        """One-line description of an entry, with how it will reach the model."""
        parts = [entry["description"][:60]] if entry["description"] else []
        flags = []
        if entry.get("always"):
            flags.append("always in context")
        keywords = entry.get("keywords") or []
        if keywords:
            flags.append("triggers: " + ", ".join(keywords[:4]) + ("…" if len(keywords) > 4 else ""))
        if flags:
            parts.append("[" + "; ".join(flags) + "]")
        return "  ".join(parts) or "(no description)"

    # -------------------------------------------------------------- list mgmt

    def _entries(self) -> list[dict[str, Any]]:
        return self.config.setdefault("knowledge", [])

    def _refresh_list(self) -> None:
        scroll = self.query_one("#k_entries", VerticalScroll)
        scroll.remove_children()
        if not self._entries():
            scroll.mount(Static("(no knowledge files yet)", classes="k_desc"))
            return
        for i, entry in enumerate(self._entries()):
            scroll.mount(
                Horizontal(
                    Checkbox(entry["file"].replace("[", "\\["), value=bool(entry["enabled"]), id=f"k_en_{i}"),
                    Static(self._entry_summary(entry), classes="k_desc", markup=False),
                    Button("x", id=f"k_rm_{i}", variant="error"),
                    classes="k_entry",
                )
            )

    def _persist(self) -> None:
        try:
            save_config(self.config)
        except OSError as exc:
            self.notify(f"Could not save config: {exc}", severity="error")

    # ---------------------------------------------------------------- events

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        toggle = getattr(event, "toggle_button", None) or getattr(event, "_sender", None)
        sender_id = getattr(toggle, "id", "") or ""
        if sender_id.startswith("k_en_"):
            idx = int(sender_id[len("k_en_"):])
            self._entries()[idx]["enabled"] = bool(event.value)
            self._persist()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "k_path":
            exists = self._path_ok(event.value.strip())
            event.input.styles.color = "green" if exists else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "k_close":
            self.dismiss(None)
        elif bid == "k_browse":
            start = ".sekka" if os.path.isdir(".sekka") else "."
            self.app.push_screen(FileBrowseScreen(start), self._got_path)
        elif bid == "k_add":
            path = self.query_one("#k_path", Input).value.strip()
            desc = self.query_one("#k_desc", Input).value.strip()
            if not path:
                self.notify("Enter a file path first.", severity="error")
                return
            if not self._path_ok(path):
                self.notify("That file does not exist (path must be green).", severity="error")
                return
            keywords = [
                k.strip()
                for k in self.query_one("#k_keywords", Input).value.split(",")
                if k.strip()
            ]
            always = bool(self.query_one("#k_always", Checkbox).value)
            entry: dict[str, Any] = {"file": path, "description": desc, "enabled": False}
            if keywords:
                entry["keywords"] = keywords
            if always:
                entry["always"] = True
            self._entries().append(entry)
            for widget_id in ("#k_path", "#k_desc", "#k_keywords"):
                self.query_one(widget_id, Input).value = ""
            self.query_one("#k_always", Checkbox).value = False
            self._refresh_list()
            self._persist()
        elif bid.startswith("k_rm_"):
            idx = int(bid[len("k_rm_"):])
            del self._entries()[idx]
            self._refresh_list()
            self._persist()

    def _path_ok(self, raw: str) -> bool:
        """Accept paths relative to the campaign/config folder, not just to the cwd."""
        if not raw.strip():
            return False
        return self.config.resolve_path(raw).is_file()

    def _got_path(self, path: Optional[str]) -> None:
        if path:
            inp = self.query_one("#k_path", Input)
            inp.value = path


class SekkaApp(App):
    """Main application."""

    CSS = """
    #history {
        height: $sekka-history-fr;
        width: 1fr;
        padding: 0 1;
    }
    #input_panel {
        height: $sekka-input-fr;
        width: 1fr;
        border: round $sekka-border;
        padding: 0 1;
    }
    TextArea {
        width: 1fr;
        height: 1fr;
        border: none;
    }
    Static.msg-user { color: $sekka-user; }
    Static.msg-assistant { color: $sekka-assistant; }
    Static.msg-system { color: $sekka-system; }
    Static.msg-stats { color: $sekka-stats; }
    Static.msg-error { color: $sekka-error; }
    Static.msg-reasoning { color: $sekka-stats; text-style: italic; }
    Static.msg-tool { color: $sekka-system; }
    #status_row { dock: bottom; height: 1; }
    #ctx_notice { width: 1fr; }
    #ctx_status { width: auto; text-align: right; }
    """

    HIDDEN_KINDS = ("reasoning", "tool")

    BINDINGS = [
        Binding("ctrl+c", "quit_armed", "Quit (twice)", priority=True),
        Binding("ctrl+t", "toggle_thinking", "Thinking"),
        Binding("ctrl+x", "stop_generation", "Stop reply", priority=True),
        Binding("ctrl+o", "toggle_ooc", "OOC mode"),
        Binding("escape", "clear_input", "Clear input"),
    ]

    def __init__(self, config: Config, resume: Optional[str] = None) -> None:
        self.config = config
        # None = no resume, "" = show the session picker, otherwise a file path
        self.resume = resume
        super().__init__()
        self.chat: list[dict[str, str]] = []  # context sent to the model (no system)
        self.full_chat: list[dict[str, str]] = []  # everything said, for /save
        self.notice_text = ""
        self.summary = ""  # compacted/restored earlier context, folded into the system prompt
        self.lore: dict[str, str] = {}  # knowledge already read this session {path: text}
        self.autosave_path: Optional[Path] = None
        self.busy = False
        self._stop_event = threading.Event()  # aborts an in-flight streamed reply
        self._stream_widget: Optional[Static] = None  # live assistant line while streaming
        self._stream_text = ""
        self._thinking_reasoning_widget: Optional[Static] = None
        self.turn_alts: list[str] = []  # generated versions of the last reply, for /swipe
        self.alt_index = 0
        self._regen_pending = False
        self.ooc_mode = False          # ctrl+o: every message until toggled off
        self._pending_dice: list[str] = []  # /roll results awaiting the next message
        self.show_thinking = False  # ctrl+t / /thinking; always off at startup
        self._quit_arm = 0.0
        self.context_used = 0  # exact after a reply (usage), else estimate
        self.context_total: Optional[int] = None
        self._model_infos: dict[str, client.ModelInfo] = {}
        self.ctx_label = ""
        self._thinking: Optional[Static] = None
        self._thinking_frame = 0
        self._thinking_label = ""
        self._thinking_started = 0.0
        self._thinking_timer = None
        self.ui_lines: list[tuple[str, str]] = []  # (role, text) log, handy for tests

    def get_css_variables(self) -> dict[str, str]:
        """Expose the configured theme colors to the stylesheet."""
        variables = super().get_css_variables()
        theme = getattr(self, "config", None)
        theme = theme.theme if theme is not None else DEFAULT_CONFIG["theme"]
        variables["sekka-user"] = _hex(theme["user"])
        variables["sekka-assistant"] = _hex(theme["assistant"])
        variables["sekka-system"] = _hex(theme["system"])
        variables["sekka-stats"] = _hex(theme["stats"])
        variables["sekka-error"] = _hex(theme["error"])
        variables["sekka-border"] = _hex(theme["system"])
        percent = getattr(self, "config", None)
        percent = percent.get("history_percent", 80) if percent is not None else 80
        variables["sekka-history-fr"] = f"{percent}fr"
        variables["sekka-input-fr"] = f"{100 - percent}fr"
        return variables

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="history")
        with Vertical(id="input_panel"):
            yield ChatInput(
                keymap=self.config.keys,
                id="input",
                soft_wrap=True,
            )
            # status row docked at the bottom of the editor box:
            # context notices left, context meter right
            with Horizontal(id="status_row"):
                yield Static(id="ctx_notice", classes="msg-stats")
                yield Static(id="ctx_status", classes="msg-stats")

    def on_mount(self) -> None:
        self.register_theme(SEKKA_THEME)
        self.theme = "sekka"
        title = "sekka"
        model = self.config["model"]
        if model:
            title += f" - {model}"
        self.title = title
        self.query_one("#input", TextArea).focus()
        theme = self.config.theme
        self._append(
            f"sekka - endpoint {self.config['endpoint']}"
            + (f" - model {model}" if model else " (no model selected)")
            + "\nType /help for commands."
            + ("\nRead-only mode: settings are locked, nothing is written to disk."
               if self._read_only else ""),
            "system",
        )
        self._remember_cli_flags()
        if not model:
            self._ensure_models(refresh=False)
        elif not self.config.get("context_window"):
            self._fetch_context_size(model)
        self._apply_context_total()
        self._update_ctx_label()
        if self.resume == "":
            self._open_resume_picker()
        elif self.resume:
            self._load_session(self.resume)
        else:
            self._load_always_lore()
            self._maybe_greet()

    @property
    def _served(self) -> bool:
        """True when this process is being shown by `sekka serve` (textual-serve)."""
        return os.environ.get("TERM_PROGRAM") == "textual"

    @property
    def _read_only(self) -> bool:
        return bool(self.config.get("readonly", False))

    def _blocked(self, what: str) -> bool:
        """True when `what` must not happen; tells the user why when it is refused."""
        if not self._read_only:
            return False
        self._sys(
            f"Read-only mode: {what} is disabled.\n"
            "Chat, /roll, /ooc, /note, /undo, /edit, /regen, /swipe, /save all still work."
        )
        return True

    def _show_read_only_summary(self) -> None:
        """What /config becomes in read-only mode: facts, no fields, no secrets."""
        cfg = self.config
        campaign = cfg.campaign_path
        lines = [
            "Read-only mode (settings are locked):",
            f"  endpoint: {cfg.get('endpoint') or '(not set)'}",
            f"  model:    {cfg.get('model') or '(not selected)'}",
            f"  campaign: {cfg.get('name') or (campaign.stem if campaign else '(none)')}",
            f"  labels:   {cfg.get('labels', {}).get('user')} / "
            f"{cfg.get('labels', {}).get('assistant')}",
            f"  stream:   {'on' if cfg.get('stream') else 'off'}"
            f"    context: {cfg.get('context_mode')}"
            f"    thinking: {'shown' if cfg.get('show_reasoning') else 'hidden'}",
            "  (the API key is never shown here; /play for the command cheat sheet)",
        ]
        self._sys("\n".join(lines))

    def _remember_cli_flags(self) -> None:
        """`sekka --endpoint URL` once per directory, then bare `sekka` works."""
        if self._read_only:
            return
        try:
            path, keys = remember_cli_values(self.config)
        except (ConfigError, OSError) as exc:
            self._sys(f"Could not remember these settings: {exc}", error=True)
            return
        if path is None:
            return
        what = " and ".join(keys)
        self._sys(
            f"Remembered {what} in {path} - next time just run: sekka"
            "  (use --no-remember to skip this)"
        )

    def _maybe_greet(self) -> None:
        """A campaign's opening line opens the scene (and enters the context)."""
        greeting = (self.config.get("greeting") or "").strip()
        if not greeting or self.full_chat:
            return
        message = {"role": "assistant", "content": greeting}
        self.chat.append(dict(message))
        self.full_chat.append(dict(message))
        self._append(self._display_text(message)[0], "assistant")
        self.turn_alts = [greeting]
        self._update_ctx_label()

    @work(exclusive=True, group="models")
    async def _fetch_context_size(self, model: str) -> None:
        """Silently fetch model metadata so the meter knows the context size."""
        cfg = self.config.values
        try:
            models = await asyncio.to_thread(
                client.list_models, cfg["endpoint"], cfg.get("api_key", "")
            )
        except client.ClientError:
            return  # meter stays '?/' - not fatal
        self._model_infos = {m.id: m for m in models}
        self._apply_context_total()
        self._update_ctx_label()

    # ------------------------------------------------------------- context meter

    def _context_limit(self) -> Optional[int]:
        """Token budget for conversation content, reserving room for the reply."""
        total = self.context_total
        if not total:
            return None
        reserve = max(256, total // 10)
        return total - reserve

    def _system_text(self) -> str:
        """One system message: prompt + summary of earlier turns + loaded lore.

        Everything that is not a user/assistant turn lives here because many
        chat templates reject system messages anywhere but first.
        """
        parts = [(self.config["system_prompt"] or "").strip()]
        player = (self.config.get("player") or "").strip()
        if player:
            parts.append("[The player's character]\n" + player)
        if self.summary:
            parts.append("[Summary of earlier conversation]\n" + self.summary)
        if self.lore:
            blocks = [
                f"--- {Path(p).name} ---\n{text}" for p, text in self.lore.items()
            ]
            parts.append("[Reference material already loaded]\n" + "\n\n".join(blocks))
        note = (self.config.get("note") or "").strip()
        if note:
            # last, because author's notes and running state want the model's attention
            parts.append("[Author's note - keep this current and honoured]\n" + note)
        return "\n\n".join(p for p in parts if p)

    def _used_estimate(self) -> int:
        system = self._system_text()
        est = estimate_tokens(system) if system else 0
        est += sum(estimate_tokens(m["content"]) for m in self.chat)
        return max(self.context_used, est)

    # -------------------------------------------------------------- ctx status

    def _set_notice(self, text: str) -> None:
        self.notice_text = text
        try:
            self.query_one("#ctx_notice", Static).update(f" {text} " if text else "")
        except Exception:
            pass  # before mount

    def _update_ctx_label(self) -> None:
        self.ctx_label = f"{format_tokens(self._used_estimate())}/{format_tokens(self.context_total)}"
        try:
            self.query_one("#ctx_status", Static).update(f" {self.ctx_label} ")
        except Exception:
            pass  # before mount

    def _load_always_lore(self) -> None:
        """Entries marked 'always' go into the context before the first message."""
        loaded = []
        for entry in self._enabled_knowledge():
            if not entry.get("always"):
                continue
            path = str(self.config.resolve_path(entry["file"]))
            if path in self.lore:
                continue
            text = self._read_knowledge_file(path)
            if text.startswith("Error:"):
                self._sys(f"Knowledge file {entry['file']} could not be read: {text}", error=True)
                continue
            self._load_into_lore(path, text)
            loaded.append(Path(entry["file"]).name)
        if loaded:
            self._set_notice(f"always-on lore loaded: {', '.join(loaded)}")

    def _trigger_lore(self, text: str) -> list[str]:
        """Lorebook style: keywords in the player's message pull lore in.

        Works on any model (no tool calling needed) and costs no extra round
        trip, because the text joins the system prompt for this and later turns.
        """
        haystack = text.casefold()
        loaded = []
        for entry in self._enabled_knowledge():
            keywords = [k for k in entry.get("keywords", []) if isinstance(k, str)]
            if not keywords:
                continue
            path = str(self.config.resolve_path(entry["file"]))
            if path in self.lore:
                continue
            if not any(keyword.casefold().strip() in haystack for keyword in keywords):
                continue
            content = self._read_knowledge_file(path)
            if content.startswith("Error:"):
                self._sys(f"Knowledge file {entry['file']} could not be read: {content}", error=True)
                continue
            self._load_into_lore(path, content)
            loaded.append(Path(entry["file"]).name)
        if loaded:
            self._set_notice(f"lore loaded: {', '.join(loaded)}")
        return loaded

    # ------------------------------------------------------------- ui helpers

    def _history(self) -> VerticalScroll:
        return self.query_one("#history", VerticalScroll)

    def _display_text(self, msg: dict[str, str]) -> tuple[str, str]:
        """(text, css class) for one stored message, as it should appear."""
        role, content = msg["role"], msg["content"]
        labels = self.config["labels"]
        if role == "user":
            return f"{labels['user']}:\n{content}", "user"
        if role == "assistant":
            shown = content.strip("\n") if content.strip() else content
            return f"{labels['assistant']}:\n{shown}", "assistant"
        return f"(context note)\n{content}", "system"

    def _rebuild_history(self) -> None:
        """Redraw the visible log from full_chat in one batch (after edits/undo/resume)."""
        history = self._history()
        history.remove_children()
        self.ui_lines = []
        widgets = []
        for msg in self.full_chat:
            text, kind = self._display_text(msg)
            self.ui_lines.append((kind, text))
            widget = Static(text, classes=f"msg-{kind}", markup=False)
            if kind in self.HIDDEN_KINDS:
                widget.styles.display = "block" if self.show_thinking else "none"
            widgets.append(widget)
        if widgets:
            history.mount(*widgets)  # one batch: resuming long sessions stays fast
        history.scroll_end(animate=False)
        self._update_ctx_label()

    def _at_bottom(self) -> bool:
        """True when the history view sits at the end (nothing left to read above)."""
        h = self._history()
        return h.scroll_y >= h.virtual_size.height - h.region.height - 1

    def _append(self, text: str, role: str, log: bool = True) -> Static:
        widget = Static(text, classes=f"msg-{role}", markup=False)
        if role in self.HIDDEN_KINDS:
            widget.styles.display = "block" if self.show_thinking else "none"
        if log:
            self.ui_lines.append((role, text))
        pinned = self._at_bottom()  # don't yank the view down if the user scrolled up
        self._history().mount(widget)
        if pinned:
            self._history().scroll_end(animate=False)
        return widget

    def _sys(self, text: str, error: bool = False) -> None:
        self._append(text, "error" if error else "system")

    def action_history_scroll_up(self) -> None:
        self._history().scroll_page_up(animate=False)

    def action_history_scroll_down(self) -> None:
        self._history().scroll_page_down(animate=False)

    # ------------------------------------------------------- keys / toggles

    def action_quit_armed(self) -> None:
        """ctrl+c: copy when text is selected, otherwise quit on 2nd press."""
        widget = self.focused
        if isinstance(widget, TextArea):
            selection = getattr(widget, "selection", None)
            if selection is not None and not selection.is_empty:
                try:
                    widget.action_copy()
                except Exception:
                    pass
                return
        now = time.monotonic()
        if now - self._quit_arm <= 2.0:
            self.exit()
            return
        self._quit_arm = now
        self.notify("Press ctrl+c again to quit", timeout=2.0)

    def action_stop_generation(self) -> None:
        """ctrl+x: keep whatever has streamed so far, drop the rest of the reply."""
        if not self.busy:
            self.notify("Nothing is being generated.", timeout=2.0)
            return
        self._stop_event.set()
        self.notify("Stopping reply...", timeout=2.0)

    def action_clear_input(self) -> None:
        editor = self.query_one("#input", ChatInput)
        if editor.text:
            editor.clear()

    def action_toggle_thinking(self) -> None:
        self.show_thinking = not self.show_thinking
        for widget in self.query(Static):
            classes = widget.classes
            if "msg-reasoning" in classes or "msg-tool" in classes:
                widget.styles.display = "block" if self.show_thinking else "none"
        self.notify(
            "thinking & tool calls shown" if self.show_thinking
            else "thinking & tool calls hidden",
            timeout=2.0,
        )

    # -------------------------------------------------------------- submit flow

    def on_submit(self, message: Submit) -> None:
        message.stop()
        editor = self.query_one("#input", TextArea)
        raw = editor.text
        if not raw.strip():
            self.bell()
            return
        parsed = commands.parse_input(raw)
        editor.load_text("")
        if parsed.kind == "text":
            text = parsed.text.strip()
            if self.ooc_mode:
                text = f"(OOC: {text})"
            self._send_chat(text)
        else:
            self._handle_command(parsed.name, parsed.arg)

    def _roll_context(self, need: int, limit: int) -> int:
        """Drop oldest turns (keeping the latest exchange) until the budget fits."""
        dropped = 0
        while len(self.chat) > 2 and need > limit:
            removed = self.chat.pop(0)
            need -= estimate_tokens(removed["content"])
            dropped += 1
            # never leave the context starting with an assistant turn
            while len(self.chat) > 2 and self.chat[0]["role"] != "user":
                need -= estimate_tokens(self.chat.pop(0)["content"])
                dropped += 1
        self.context_used = 0  # exact count of the old window is stale now
        self._update_ctx_label()
        return dropped

    @work(exclusive=True, group="chat")
    async def _compact_then_send(self, text: str) -> None:
        cfg = self.config.values
        limit = self._context_limit() or 0
        keep_budget = max(512, limit // 2)
        tail_start = len(self.chat)
        acc = estimate_tokens(text)
        while tail_start > 0:
            cost = estimate_tokens(self.chat[tail_start - 1]["content"])
            if acc + cost > keep_budget and len(self.chat) - tail_start >= 2:
                break
            acc += cost
            tail_start -= 1
        while tail_start < len(self.chat) - 1 and self.chat[tail_start]["role"] != "user":
            tail_start += 1  # keep the kept tail starting on a user turn
        older, newer = self.chat[:tail_start], self.chat[tail_start:]
        if not older:  # nothing worth summarizing; just proceed
            self._stop_thinking()
            self.busy = False
            self._send_chat(text)
            return
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in older)
        if self.summary:
            transcript = f"(earlier summary) {self.summary}\n" + transcript
        try:
            resp = await asyncio.to_thread(
                client.chat_completion,
                cfg["endpoint"], cfg["model"],
                [
                    {"role": "system", "content": "You compress conversations."},
                    {"role": "user", "content":
                     "Summarize the following conversation so it can continue seamlessly, "
                     f"in at most 150 words:\n\n{transcript}"},
                ],
                api_key=cfg.get("api_key", ""), temperature=0.3,
                timeout=cfg.get("request_timeout", 300),
                reasoning_effort="none",  # summarizing does not need thinking
            )
        except client.ClientError as exc:
            self._stop_thinking()
            self.busy = False
            dropped = self._roll_context(self._used_estimate() + estimate_tokens(text), limit)
            self._set_notice(
                f"summary failed; rolled {dropped} message(s)" if dropped
                else "summary failed"
            )
            self._send_chat(text)
            return
        self.summary = resp.content.strip()
        self.chat[:] = newer
        self.context_used = 0
        self._stop_thinking()
        self.busy = False
        self._set_notice(f"compacted {len(older)} message(s) into summary")
        self._update_ctx_label()
        self._send_chat(text)

    def _send_chat(self, text: str) -> None:
        if self._pending_dice:
            # dice land with the action they belong to, where the model can see them
            text = "\n".join(self._pending_dice) + "\n" + text
            self._pending_dice = []
        if self.busy:
            self.notify("Still waiting for the current reply.", severity="warning")
            return
        model = self.config["model"]
        if not model:
            self._sys("No model selected. Use /models or /config first.", error=True)
            return
        limit = self._context_limit()
        if limit:
            need = self._used_estimate() + estimate_tokens(text)
            if need > limit:
                mode = self.config["context_mode"]
                if mode == "pause":
                    self._sys(
                        f"Context window full ({format_tokens(need)}/{format_tokens(self.context_total)} tokens).\n"
                        "Options: /save then /clear, or switch context_mode to "
                        "'rolling'/'compact' in /config.",
                        error=True,
                    )
                    return
                if mode == "rolling":
                    dropped = self._roll_context(need, limit)
                    if dropped:
                        self._set_notice(f"dropped {dropped} oldest message(s)")
                elif mode == "compact":
                    self.busy = True
                    self._start_thinking("compacting")
                    self._compact_then_send(text)
                    return
        self.chat.append({"role": "user", "content": text})
        self.full_chat.append({"role": "user", "content": text})
        user_label = self.config["labels"]["user"]
        self._append(f"{user_label}:\n{text}", "user")
        self._trigger_lore(text)  # before the request: triggered lore rides along
        self._start_reply()

    def _start_reply(self) -> None:
        """Generate a reply for whatever is currently at the end of the context."""
        self.busy = True
        self._start_thinking()
        self._chat_worker()

    # ------------------------------------------------------- thinking spinner

    SNOWFLAKE_FRAMES = ("\u2744", "\u2745", "\u2746", "\u2745")  # ❄ ❅ ❆ ❅

    def _thinking_text(self) -> str:
        glyph = self.SNOWFLAKE_FRAMES[self._thinking_frame % len(self.SNOWFLAKE_FRAMES)]
        elapsed = time.monotonic() - self._thinking_started
        label = self._thinking_label
        if label:
            return f"{glyph}  {label} {elapsed:.0f}s"
        return f"{glyph}  {elapsed:.0f}s"

    def _start_thinking(self, label: str = "") -> None:
        self._thinking_frame = 0
        self._thinking_label = label
        self._thinking_started = time.monotonic()
        self._thinking = self._append(self._thinking_text(), "stats", log=False)
        self._thinking_timer = self.set_interval(0.15, self._advance_thinking)

    def _advance_thinking(self) -> None:
        self._thinking_frame += 1
        if self._thinking is not None and self._thinking.is_mounted:
            self._thinking.update(self._thinking_text())

    def _stop_thinking(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer = None
        if self._thinking is not None:
            self._thinking.remove()
            self._thinking = None

    # ------------------------------------------------------- knowledge tools

    KNOWLEDGE_TOOL_ROUNDS = 8
    KNOWLEDGE_MAX_BYTES = 256_000
    LORE_MAX_TOTAL_CHARS = 60_000  # cap on lore folded into the system prompt

    def _enabled_knowledge(self) -> list[dict[str, Any]]:
        return [e for e in self.config.get("knowledge", []) if e.get("enabled")]

    def _load_into_lore(self, path: str, text: str) -> None:
        """Remember reference material for the rest of the session (with a cap)."""
        self.lore[path] = text
        total = sum(len(v) for v in self.lore.values())
        while total > self.LORE_MAX_TOTAL_CHARS and len(self.lore) > 1:
            oldest = next(iter(self.lore))
            total -= len(self.lore.pop(oldest))
        self._update_ctx_label()

    def _knowledge_tools(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Build tool schemas from enabled knowledge entries.

        Returns (schemas, {tool_name: resolved_path}). Security: the model can
        only reach files already listed (and enabled) in the config - tools
        take no arguments, so it can never name a file to read itself.
        """
        schemas: list[dict[str, Any]] = []
        files: dict[str, str] = {}
        used: set[str] = set()
        for i, entry in enumerate(self._enabled_knowledge()):
            path = str(self.config.resolve_path(entry["file"]).resolve())
            if path in self.lore:
                continue  # already in the system context this session
            # OpenAI tool names must match [a-zA-Z0-9_-]{1,64}
            stem = re.sub(r"[^A-Za-z0-9]+", "_", Path(entry["file"]).stem).strip("_")[:50] or "knowledge"
            name = f"read_{stem}"
            if name in used:
                name = f"{name}_{i}"
            used.add(name)
            desc = (entry.get("description") or "").strip() or f"Knowledge file {entry['file']}"
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"{desc} Returns the full contents of the file.",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            )
            files[name] = path
        return schemas, files

    def _read_knowledge_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                data = fh.read(self.KNOWLEDGE_MAX_BYTES + 1)
        except OSError as exc:
            return f"Error: the knowledge file could not be read ({exc.strerror or exc})."
        if len(data) > self.KNOWLEDGE_MAX_BYTES:
            return data[: self.KNOWLEDGE_MAX_BYTES] + "\n[truncated]"
        return data

    # ------------------------------------------------------------- chat worker

    async def _stream_round(
        self,
        cfg: dict[str, Any],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> client.StreamEvent:
        """Run one streamed round: HTTP in a worker thread, UI updated on this one.

        Yields progress into an asyncio queue so the reply appears token by
        token while the socket is still being read, and so ctrl+x can abort it.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def post(value: Any) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, value)
            except RuntimeError:
                pass  # app already closed; this stream is abandoned

        def pump() -> None:
            try:
                for ev in client.stream_chat_completion(
                    cfg["endpoint"],
                    cfg["model"],
                    messages,
                    api_key=cfg.get("api_key", ""),
                    temperature=cfg.get("temperature"),
                    max_tokens=cfg.get("max_tokens"),
                    timeout=cfg.get("request_timeout", 300),
                    tools=tools or None,
                    reasoning_effort=cfg.get("reasoning", "medium"),
                    top_p=cfg.get("top_p"),
                    min_p=cfg.get("min_p"),
                    presence_penalty=cfg.get("presence_penalty"),
                    frequency_penalty=cfg.get("frequency_penalty"),
                    repetition_penalty=cfg.get("repetition_penalty"),
                    stop=cfg.get("stop") or None,
                    stop_event=self._stop_event,
                ):
                    post(ev)
            except client.ClientError as exc:
                post(exc)
            except Exception as exc:  # noqa: BLE001 - surfaced like an endpoint error
                post(client.ClientError(str(exc)))
            finally:
                post(None)

        producer = asyncio.create_task(asyncio.to_thread(pump))
        final: Optional[client.StreamEvent] = None
        progress: Optional[client.StreamEvent] = None
        abandoned = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    # An endpoint that has gone quiet has nothing to poll on and a
                    # blocked socket read cannot be interrupted portably, so we
                    # release the UI at once and let the read finish abandoned.
                    if self._stop_event.is_set():
                        abandoned = True
                        break
                    continue
                if item is None:
                    break
                if isinstance(item, client.ClientError):
                    raise item
                self._show_round(item)
                progress = item
                if item.message:  # only the closing event carries the message
                    final = item
                    if self._stop_event.is_set():
                        break
        finally:
            if abandoned:
                producer.add_done_callback(lambda task: task.exception())
            else:
                await producer
        if abandoned:
            final = progress
        if final is None:
            raise client.ClientError("Endpoint closed the stream without a reply.")
        if self._stop_event.is_set():
            final.stopped = True
        return final

    def _show_round(self, ev: client.StreamEvent) -> None:
        """Paint progress: create the assistant line on the first token, then update it."""
        label = self.config["labels"]["assistant"]
        if ev.reasoning.strip():
            widget = self._thinking_reasoning_widget
            if widget is None or not widget.is_mounted:
                widget = self._append(f"{label} thinking:\n{ev.reasoning.strip()}", "reasoning")
                self._thinking_reasoning_widget = widget
            else:
                widget.update(f"{label} thinking:\n{ev.reasoning.strip()}")
        if ev.content:
            if self._stream_widget is None:
                self._stop_thinking()  # first tokens beat the snowflake
                # logged separately once the round settles, with the final text
                self._stream_widget = self._append(f"{label}:\n{ev.content}", "assistant", log=False)
                self._stream_text = ev.content
            elif ev.content != self._stream_text:
                self._stream_widget.update(f"{label}:\n{ev.content}")
                self._stream_text = ev.content
                if self._at_bottom():
                    self._history().scroll_end(animate=False)

    @work(exclusive=True, group="chat")
    async def _chat_worker(self) -> None:
        """Send the conversation; streamed replies land in the view as they arrive."""
        cfg = self.config.values
        messages: list[dict[str, Any]] = []
        system_prompt = self._system_text()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self.chat)
        assistant_label = self.config["labels"]["assistant"]
        tools, tool_files = self._knowledge_tools()
        loaded_now: dict[str, str] = {}
        rounds = 0
        total_completion = 0
        last_prompt: Optional[int] = None
        last_elapsed = 0.0
        stopped = False
        self._stop_event.clear()
        self._stream_widget = None
        self._stream_text = ""
        self._thinking_reasoning_widget = None
        try:
            while True:
                if cfg.get("stream", True):
                    resp = await self._stream_round(cfg, messages, tools)
                else:
                    r = await asyncio.to_thread(
                        client.chat_completion,
                        cfg["endpoint"],
                        cfg["model"],
                        messages,
                        api_key=cfg.get("api_key", ""),
                        temperature=cfg.get("temperature"),
                        max_tokens=cfg.get("max_tokens"),
                        timeout=cfg.get("request_timeout", 300),
                        tools=tools or None,
                        reasoning_effort=cfg.get("reasoning", "medium"),
                        top_p=cfg.get("top_p"),
                        min_p=cfg.get("min_p"),
                        presence_penalty=cfg.get("presence_penalty"),
                        frequency_penalty=cfg.get("frequency_penalty"),
                        repetition_penalty=cfg.get("repetition_penalty"),
                        stop=cfg.get("stop") or None,
                    )
                    resp = client.StreamEvent(
                        content=r.content,
                        reasoning=r.reasoning,
                        tool_calls=r.tool_calls,
                        message=r.message,
                        prompt_tokens=r.prompt_tokens,
                        completion_tokens=r.completion_tokens,
                        elapsed=r.elapsed,
                        finish_reason=r.finish_reason,
                    )
                    self._show_round(resp)
                stopped = stopped or resp.stopped
                if resp.prompt_tokens is not None:
                    last_prompt = resp.prompt_tokens
                total_completion += resp.completion_tokens or 0
                last_elapsed += resp.elapsed
                if (
                    resp.tool_calls
                    and tool_files
                    and rounds < self.KNOWLEDGE_TOOL_ROUNDS
                    and not stopped
                ):
                    if resp.content.strip():
                        # preamble the model wrote alongside the tool call
                        self.ui_lines.append(
                            ("assistant", f"{assistant_label}:\n{resp.content.strip('\n')}"),
                        )
                    rounds += 1
                    messages.append(
                        resp.message
                        if resp.message
                        else {"role": "assistant", "content": None, "tool_calls": resp.tool_calls}
                    )
                    for call in resp.tool_calls:
                        fn = str((call.get("function") or {}).get("name") or "?")
                        self._append(f"tool call: {fn}", "tool")
                        if fn in tool_files:
                            result = self._read_knowledge_file(tool_files[fn])
                            loaded_now[tool_files[fn]] = result
                        else:
                            # model hallucinated a tool: refuse, do not read anything
                            result = "Error: unknown tool."
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id", ""),
                                "content": result,
                            }
                        )
                    continue
                break
        except client.ClientError as exc:
            self._stop_thinking()
            self.busy = False
            self._sys(f"Error: {exc}", error=True)
            # roll back the unanswered user turn so history stays consistent
            if self.chat and self.chat[-1]["role"] == "user":
                self.chat.pop()
            if self.full_chat and self.full_chat[-1]["role"] == "user":
                self.full_chat.pop()
            self._update_ctx_label()
            return

        self._stop_thinking()
        self.busy = False
        if not stopped and not resp.tool_calls and not resp.content.strip():
            # a blank turn is a failure worth reporting, not an empty message to store
            if resp.finish_reason == "length" and resp.reasoning.strip():
                hint = (
                    "the model spent the whole reply budget on thinking - raise "
                    "max tokens, or set reasoning to none"
                )
            elif resp.finish_reason == "length":
                hint = "the reply hit the max tokens limit before any text"
            else:
                hint = "the endpoint returned no text"
            self._sys(f"Empty reply ({hint}). Your message was kept; try again.", error=True)
            self._update_ctx_label()
            return
        # keep lore the model read so later turns need not re-fetch it
        self.lore.update(loaded_now)
        if last_prompt is not None:
            self.context_used = last_prompt + total_completion
        self._update_ctx_label()
        text = resp.content
        if stopped:
            text = (text + " …[stopped]") if text.strip() else "[stopped - no output]"
        self.chat.append({"role": "assistant", "content": text})
        self.full_chat.append({"role": "assistant", "content": text})
        if self._regen_pending:
            self.turn_alts.append(text)
            self._regen_pending = False
        else:
            self.turn_alts = [text]
        self.alt_index = len(self.turn_alts) - 1
        # models often pad replies with blank lines; don't render them
        shown = text.strip("\n") if text.strip() else text
        if self._stream_widget is not None and self._stream_widget.is_mounted:
            self._stream_widget.update(f"{assistant_label}:\n{shown}")  # already on screen
            self.ui_lines.append(("assistant", f"{assistant_label}:\n{shown}"))
        else:
            self._append(f"{assistant_label}:\n{shown}", "assistant")
        self._append(format_stats(last_elapsed, total_completion), "stats")
        if self.config.get("autosave") and self.full_chat and not self._read_only:
            path = self._save_history(autosave=True)
            if path:
                self._append(f"(autosaved to {path})", "stats")
        if self._at_bottom():
            self._history().scroll_end(animate=False)

    # --------------------------------------------------------------- commands

    def _handle_command(self, name: str, arg: str = "") -> None:
        command = commands.resolve_command(name)
        if command is None:
            self._sys(f"Unknown command: /{name} (try /help)", error=True)
            return
        if command == "help":
            lines = ["Commands:"] + [f"  {cmd:<9} {desc}" for cmd, desc in commands.COMMAND_HELP]
            keys = self.config.keys
            lines += [
                "Keys:",
                f"  {keys['submit']:<9} send message",
                f"  {keys['newline']:<9} insert newline",
                f"  {keys['scroll_up']:<9} scroll history up",
                f"  {keys['scroll_down']:<9} scroll history down",
                "  ctrl+c    quit (press twice; copies a selection if one exists)",
                "  ctrl+t    show/hide model thinking & tool calls",
                "  ctrl+x    stop the reply being generated (keeps what arrived)",
                "  ctrl+o    out-of-character mode on/off",
                "  escape    clear the input box",
                "(roleplaying quick reference: /play)",
                "(key bindings and colors are configured in the config file)",
            ]
            self._sys("\n".join(lines))
        elif command == "save":
            self._confirm_save()
        elif command == "config":
            if self._read_only:
                self._show_read_only_summary()
                return
            self.push_screen(ConfigScreen(self.config), self._apply_config)
        elif command == "clear":
            for child in list(self._history().children):
                child.remove()
            self.chat.clear()
            self.full_chat.clear()
            self.summary = ""
            self.lore.clear()
            self.autosave_path = None
            self.turn_alts = []
            self.alt_index = 0
            self._pending_dice = []
            self.context_used = 0
            self.ui_lines.clear()
            self._set_notice("")
            self._sys("(history cleared)")
        elif command == "models":
            if self._blocked("/models"):
                return
            self._ensure_models(refresh=True)
        elif command == "knowledge":
            if self._blocked("/knowledge"):
                return
            self.push_screen(KnowledgeScreen(self.config))
        elif command == "thinking":
            self.action_toggle_thinking()
        elif command == "stop":
            self.action_stop_generation()
        elif command in ("undo", "edit", "regen", "swipe"):
            self._turn_edit(command)
        elif command == "play":
            self._sys(commands.play_help_text())
        elif command == "export":
            self._handle_export()
        elif command == "roll":
            self._handle_roll(arg)
        elif command == "ooc":
            if not arg:
                self._sys("Say something out of character: /ooc <text>", error=True)
                return
            self._send_chat(f"(OOC: {arg})")
        elif command == "note":
            self._handle_note(arg)
        elif command == "campaign":
            self._handle_campaign(arg)
        elif command == "exit":
            self.exit()

    def _drop_last_exchange(self) -> bool:
        """Remove the trailing assistant reply and the user turn that provoked it."""
        if not self.full_chat:
            return False
        if self.full_chat[-1]["role"] == "assistant":
            self.full_chat.pop()
            if self.chat and self.chat[-1]["role"] == "assistant":
                self.chat.pop()
        if self.full_chat and self.full_chat[-1]["role"] == "user":
            self.full_chat.pop()
            if self.chat and self.chat[-1]["role"] == "user":
                self.chat.pop()
        self.turn_alts = []
        self.alt_index = 0
        self._pending_dice = []
        return True

    def _turn_edit(self, command: str) -> None:
        """/undo, /edit, /regen, /swipe: the loop RP users actually live in."""
        if self.busy:
            self.notify("Wait for (or stop with ctrl+x) the current reply.", severity="warning")
            return
        if command == "undo":
            if not self._drop_last_exchange():
                self._sys("Nothing to undo.", error=True)
                return
            self._rebuild_history()
            self._sys("(last exchange removed)")
        elif command == "edit":
            last_user = next(
                (m["content"] for m in reversed(self.full_chat) if m["role"] == "user"), None
            )
            if last_user is None:
                self._sys("Nothing to edit yet.", error=True)
                return
            self._drop_last_exchange()
            self._rebuild_history()
            editor = self.query_one("#input", ChatInput)
            editor.load_text(last_user)
            lines = last_user.split("\n")
            editor.cursor = (len(lines) - 1, len(lines[-1]))  # caret after the pasted text
            self._sys("(last message back in the input - edit and press enter)")
        elif command == "regen":
            if not self.chat or self.chat[-1]["role"] != "assistant":
                self._sys("Nothing to regenerate yet.", error=True)
                return
            if not any(m["role"] == "user" for m in self.chat):
                # e.g. /regen on a campaign greeting: nothing to answer yet
                self._sys("Nothing to regenerate - there is no message to reply to.", error=True)
                return
            if not self.turn_alts:
                self.turn_alts = [self.chat[-1]["content"]]
            self.chat.pop()
            self.full_chat.pop()
            self._rebuild_history()
            self._regen_pending = True
            self._set_notice(f"regenerating (keeping {len(self.turn_alts)} version(s))")
            self._start_reply()
        elif command == "swipe":
            if len(self.turn_alts) < 2:
                self._sys("Only one version of that reply exists. /regen to make another.")
                return
            self.alt_index = (self.alt_index + 1) % len(self.turn_alts)
            text = self.turn_alts[self.alt_index]
            self.chat[-1]["content"] = text
            self.full_chat[-1]["content"] = text
            self._rebuild_history()
            self._set_notice(f"reply {self.alt_index + 1} of {len(self.turn_alts)}")

    def _handle_roll(self, arg: str) -> None:
        """Real dice, shown to the player and folded into the next message."""
        try:
            result = dice.roll(arg)
        except dice.DiceError as exc:
            self._sys(str(exc), error=True)
            return
        self._append(f"dice: {result.detail}", "stats")
        self._pending_dice.append(f"[dice] {result.detail}")
        self.notify(result.detail, timeout=4.0)

    def action_toggle_ooc(self) -> None:
        """ctrl+o: everything you type from here on is out of character."""
        self.ooc_mode = not self.ooc_mode
        self._set_notice("OOC mode on (ctrl+o to switch back)" if self.ooc_mode else "")
        self.notify("OOC mode on" if self.ooc_mode else "OOC mode off", timeout=2.0)

    def _handle_note(self, arg: str) -> None:
        """/note - the pinned block that survives compaction and long scenes.

        Inventory, injuries, promises, "the door is still barred": anything the
        model must keep straight that summarising would otherwise eat.
        """
        if not arg:
            note = (self.config.get("note") or "").strip()
            self._sys(f"Pinned note:\n{note}" if note else "No pinned note set. Use: /note <text>")
            return
        if arg == "clear":
            text = ""
        elif arg.startswith("+"):
            existing = (self.config.get("note") or "").strip()
            addition = arg[1:].strip()
            if not addition:
                self._sys("Nothing to append. Use: /note +<text>", error=True)
                return
            text = f"{existing}\n{addition}" if existing else addition
        else:
            text = arg
        if len(text) > 20_000:
            self._sys("That note is too long (20,000 character limit).", error=True)
            return
        self.config["note"] = text
        written = None
        note_read_only = self._read_only
        try:
            if text and self.config.campaign_path is not None and not note_read_only:
                written = save_campaign_values(self.config, {"note": text})
            if written is None and not note_read_only:
                save_config(self.config)
        except (OSError, ConfigError) as exc:
            self._sys(f"Note set for this session, but could not save it: {exc}", error=True)
            return
        self._update_ctx_label()
        if not text:
            self._sys("(pinned note cleared)" + (" - it was not persisted" if note_read_only else ""))
        elif note_read_only:
            self._sys(f"Pinned note set for this session only (read-only mode does not write files):\n{text}")
        else:
            self._sys(f"Pinned note {'saved to ' + str(written) if written else 'saved'}:\n{text}")

    def _confirm_save(self) -> None:
        if self._blocked("/save"):
            return
        if not self.full_chat:
            self._sys("Nothing to save.")
            return
        name = storage.timestamp_name(fmt=self.config.get("save_format", "json"))

        def done(confirmed: bool) -> None:
            if not confirmed:
                self._sys("(save cancelled)")
                return
            path = self._save_history()
            if path:
                self._sys(f"Saved {len(self.full_chat)} messages to {path}")

        self.push_screen(ConfirmScreen(f"Save chat history as {name}?"), done)

    def _handle_export(self) -> None:
        """/export - hand the transcript to the person playing, not to this machine.

        Textual's delivery API does the right thing per driver: in a terminal it
        lands in the user's downloads folder, under `sekka serve` it becomes a
        real browser download. The export holds only what is on screen - never
        the system prompt, campaign path or pinned note - so it is safe to allow
        in read-only mode, where a player may well want their story back.
        """
        if not self.full_chat:
            self._sys("Nothing to export yet.")
            return
        fmt = self.config.get("save_format", "json")
        name = storage.timestamp_name(fmt=fmt)
        if fmt == "markdown":
            from datetime import datetime

            text = storage.render_markdown(self.full_chat, datetime.now())
        else:
            # no meta: it carries campaign path and note, which belong to the host
            text = storage.session_json(self.full_chat)
        self._export_delivery_keys: dict[str, str] = getattr(self, "_export_delivery_keys", {})
        try:
            key = self.deliver_text(
                io.StringIO(text),
                save_filename=name,
                mime_type="text/markdown" if fmt == "markdown" else "application/json",
                name=name,
            )
        except Exception as exc:                     # driver refused / no driver
            self._sys(f"Could not export: {exc}", error=True)
            return
        if key is None:
            self._sys("Could not export: this terminal cannot deliver files.", error=True)
            return
        self._export_delivery_keys[key] = name
        self._sys(
            f"Exporting {len(self.full_chat)} messages as {name}"
            + (" - check your browser's downloads." if self._served else " to your downloads folder.")
        )

    def on_delivery_complete(self, event) -> None:
        name = getattr(self, "_export_delivery_keys", {}).pop(event.key, None)
        self._sys(f"Exported {name}." if name else "Export finished.")

    def on_delivery_failed(self, event) -> None:
        name = getattr(self, "_export_delivery_keys", {}).pop(event.key, None)
        self._sys(f"Export of {name or 'file'} failed: {event.exception}", error=True)

    def _session_meta(self) -> dict:
        """Enough to resume this session as itself (campaign, role labels)."""
        meta: dict = {"labels": dict(self.config["labels"])}
        note = (self.config.get("note") or "").strip()
        if note and len(note) <= storage.MAX_META_NOTE:
            meta["note"] = note
        if self.config.campaign_path is not None:
            meta["campaign"] = str(self.config.campaign_path)
        return meta

    def _save_history(self, autosave: bool = False) -> Optional[str]:
        if self.config.get("save_format", "json") != "json":
            meta = None  # markdown saves are for reading; only JSON resumes
        else:
            meta = self._session_meta()
        try:
            path = storage.save_history(
                self.full_chat,
                directory=self.config.get("save_dir", "."),
                fmt=self.config.get("save_format", "json"),
                overwrite=self.autosave_path if autosave else None,
                meta=meta,
            )
        except OSError as exc:
            self._sys(f"Could not save: {exc}", error=True)
            return None
        if autosave:
            self.autosave_path = path
        return str(path)

    def _apply_config(self, values: Optional[dict]) -> None:
        if not values:
            return
        merged = self.config.to_dict()
        merged.update(values)
        try:
            validate_config(merged)
        except ConfigError as exc:
            self._sys(f"Invalid configuration: {exc}", error=True)
            return
        campaign_edits: dict[str, Any] = {}
        for key, new in values.items():
            if self.config.values.get(key) == new:
                continue
            owned = self.config.campaign_path is not None and (
                key in self.config.campaign_keys or key == "note"
            )
            # check ownership first: release_override() forgets the campaign key
            if owned:
                campaign_edits[key] = new
            self.config.release_override(key)  # user changed it: persist it
        self.config.values.update(values)
        if campaign_edits:
            # campaign-owned values belong in the campaign, not config.json
            try:
                save_campaign_values(self.config, campaign_edits)
            except ConfigError as exc:
                self._sys(
                    f"Applied for this session, but the campaign file was not updated: {exc}",
                    error=True,
                )
        try:
            written = save_config(self.config)
        except OSError as exc:
            self._sys(f"Settings applied for this session, but could not write config: {exc}", error=True)
        else:
            self._sys(f"Configuration saved to {written}")
        if values.get("model"):
            self.title = f"sekka - {values['model']}"
        self._apply_context_total()
        self._update_ctx_label()
        if not self.config.get("context_window") and self.config["model"]:
            self._fetch_context_size(self.config["model"])
        self.query_one("#input", ChatInput).set_keymap(self.config.keys)
        self.refresh_css()

    # ------------------------------------------------------------- sessions

    def _load_session(self, path: str) -> None:
        try:
            messages, meta = storage.load_session(path)
        except storage.StorageError as exc:
            self._sys(f"Could not resume {path}: {exc}", error=True)
            return
        self._restore_session_meta(meta)
        self.chat = copy.deepcopy(messages)
        self.full_chat = copy.deepcopy(messages)
        # system notes never go mid-chat; fold them into the system prompt
        notes = [m["content"] for m in messages if m["role"] == "system"]
        self.chat = [m for m in self.chat if m["role"] != "system"]
        if notes:
            self.summary = "\n\n".join(
                n.replace("[Summary of earlier conversation]\n", "") for n in notes
            )
        self._rebuild_history()
        self._sys(f"(resumed {len(messages)} messages from {path})")

    def _restore_session_meta(self, meta: dict) -> None:
        """Re-apply the campaign/labels a session was saved with, if we can find them."""
        campaign = meta.get("campaign")
        if campaign and self.config.campaign_path is None:
            candidate = Path(campaign).expanduser()
            if candidate.is_file():
                self._apply_campaign(candidate, persist=False)
            else:
                self._sys(f"Session referenced campaign {campaign}, which is gone.", error=True)
        note = meta.get("note")
        if note:
            self.config["note"] = note  # the state as it was when saved wins
        labels = meta.get("labels")
        if labels and self.config.campaign_path is None:
            merged = {**self.config["labels"], **labels}
            self.config["labels"] = merged
            self.query_one("#input", ChatInput).set_keymap(self.config.keys)

    def _apply_campaign(self, path: Path, persist: bool = True) -> None:
        """Load a campaign file and fold it into the running configuration."""
        from .config import _load_campaign

        try:
            values = _load_campaign(path)
        except ConfigError as exc:
            self._sys(f"Could not load campaign: {exc}", error=True)
            return
        self.config.campaign_path = path
        self.config.campaign_keys = set(values)
        self.config.values = deep_merge(self.config.to_dict(), values)
        name = values.get("name") or path.stem
        self._sys(f"Campaign: {name}")
        self.query_one("#input", ChatInput).set_keymap(self.config.keys)
        self.refresh_css()
        if persist:
            self.config["campaign"] = str(path)
            try:
                save_config(self.config)
            except OSError as exc:
                self._sys(f"Campaign active for this session, but config not saved: {exc}", error=True)
        self._maybe_greet()

    def _handle_campaign(self, arg: str) -> None:
        """/campaign - show what is loaded, or switch to another one."""
        if not arg:
            if self.config.campaign_path is not None:
                self._sys(
                    f"Campaign: {self.config.get('name') or self.config.campaign_path.stem}"
                    f"\n  file: {self.config.campaign_path}"
                    f"\n  lore files: {len([e for e in self.config.get('knowledge', []) if e.get('enabled')])}"
                    f"\n/load a different one with: /campaign path/to/campaign.json"
                )
            else:
                self._sys(
                    "No campaign loaded. Start one with /campaign path/to/campaign.json"
                    " (see examples/roleplaying/ for the shape)."
                )
            return
        path = self.config.resolve_path(arg)
        if self._blocked("switching campaigns"):
            return
        if not path.is_file():
            self._sys(f"No campaign file at {path}", error=True)
            return
        if self.full_chat:
            self._sys("(campaign switched mid-scene: earlier turns keep their old framing)")
        self._apply_campaign(path)
        self._update_ctx_label()

    def _open_resume_picker(self) -> None:
        if self._read_only:
            self._sys(
                "Read-only mode: the session picker lists everyone's saved chats, so it is "
                "disabled. Ask the host to resume a session for you."
            )
            return
        directory = Path(self.config.get("save_dir", ".")).expanduser()
        sessions = storage.list_sessions(directory)
        if not sessions:
            self._sys(f"No saved sessions found in {directory}.", error=True)
            return

        def chosen(path: Optional[str]) -> None:
            if path:
                self._load_session(path)

        self.push_screen(ResumeScreen(sessions, directory), chosen)

    # ------------------------------------------------------------ model pick

    @work(exclusive=True, group="models")
    async def _ensure_models(self, refresh: bool) -> None:
        cfg = self.config.values
        self._sys("Fetching models from endpoint\u2026")
        try:
            models = await asyncio.to_thread(
                client.list_models, cfg["endpoint"], cfg.get("api_key", "")
            )
        except client.ClientError as exc:
            self._sys(f"Could not fetch models: {exc}\nSet a model with --model or /config.", error=True)
            return
        self._model_infos = {m.id: m for m in models}
        ids = [m.id for m in models]
        if not ids:
            self._sys("Endpoint reported no models. Set one with --model or /config.", error=True)
            return
        if len(ids) == 1:
            self._select_model(ids[0])
            return
        chosen = await self.push_screen_wait(ModelScreen(ids))
        if chosen:
            # a deliberate pick from a list is worth remembering; silently
            # pinning the only model an endpoint happens to host is not
            self._select_model(chosen, persist=True)
        elif refresh:
            self._sys("(kept current model)")

    def _select_model(self, model: str, persist: bool = False) -> None:
        self.config["model"] = model
        self.title = f"sekka - {model}"
        self._apply_context_total()
        self._sys(f"Model selected: {model}")
        if persist:
            try:
                path = save_config(self.config)
                self._sys(f"(model remembered in {path})")
            except (OSError, ConfigError) as exc:
                self._sys(f"Model chosen for this session, but not saved: {exc}", error=True)

    def _apply_context_total(self) -> None:
        """Context size: explicit config wins, else the endpoint's max_model_len."""
        window = self.config.get("context_window")
        if window:
            self.context_total = window
            return
        info = self._model_infos.get(self.config["model"])
        self.context_total = info.max_model_len if info else None
        self._update_ctx_label()


def _blank_if_none(value: Any) -> str:
    return "" if value is None else str(value)
