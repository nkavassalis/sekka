"""The Sekka terminal UI (built on Textual).

Layout: chat history takes the top of the screen (config.history_percent,
default 80) and the multi-line input editor the remainder. Arrow keys move
inside the input editor; PageUp/PageDown scroll the history (both
configurable).
"""

from __future__ import annotations

import asyncio
import copy
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from rich.color import Color
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DirectoryTree, Input, Label, ListItem, ListView, Select, Static, TextArea

from . import client, commands, storage
from .config import DEFAULT_CONFIG, Config, save_config, validate_config, ConfigError
from .stats import estimate_tokens, format_stats, format_tokens


def _hex(color: str) -> str:
    """Convert any rich-recognised color name to a hex string for Textual CSS."""
    return Color.parse(color).get_truecolor().hex


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
                *[ListItem(Label(name, id=f"model-item-{i}")) for i, name in enumerate(self.models)],
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
            yield Static(self.question)
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
                yield Label("Temperature (blank = endpoint default)")
                yield Input(value=_blank_if_none(values.get("temperature")), id="cfg_temperature")
                yield Label("Max tokens (blank = endpoint default)")
                yield Input(value=_blank_if_none(values.get("max_tokens")), id="cfg_max_tokens")
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
                "temperature": temperature,
                "max_tokens": max_tokens,
                "history_percent": history_percent if history_percent is not None else self.config.get("history_percent", 80),
                "context_window": context_window,
                "context_mode": str(self.query_one("#cfg_context_mode", Select).value),
                "request_timeout": timeout if timeout is not None else self.config.get("request_timeout", 300),
                "reasoning": str(self.query_one("#cfg_reasoning", Select).value),
                "save_dir": self.query_one("#cfg_save_dir", Input).value.strip() or ".",
                "save_format": str(self.query_one("#cfg_save_format", Select).value),
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
                    ListItem(Label(f"{p.name}  ({p.stat().st_size // 1024} KB)", id=f"session-item-{i}"))
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
            with Horizontal(id="k_close_row"):
                yield Button("add", id="k_add", variant="primary")
                yield Button("close", id="k_close", variant="default")

    def on_show(self) -> None:
        self._refresh_list()

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
                    Checkbox(entry["file"], value=bool(entry["enabled"]), id=f"k_en_{i}"),
                    Static(entry["description"][:60], classes="k_desc"),
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
            exists = os.path.isfile(os.path.expanduser(event.value.strip()))
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
            if not os.path.isfile(os.path.expanduser(path)):
                self.notify("That file does not exist (path must be green).", severity="error")
                return
            self._entries().append({"file": path, "description": desc, "enabled": False})
            self.query_one("#k_path", Input).value = ""
            self.query_one("#k_desc", Input).value = ""
            self._refresh_list()
            self._persist()
        elif bid.startswith("k_rm_"):
            idx = int(bid[len("k_rm_"):])
            del self._entries()[idx]
            self._refresh_list()
            self._persist()

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
        self.busy = False
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
            + "\nType /help for commands.",
            "system",
        )
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

    def _used_estimate(self) -> int:
        system = (self.config["system_prompt"] or "").strip()
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

    # ------------------------------------------------------------- ui helpers

    def _history(self) -> VerticalScroll:
        return self.query_one("#history", VerticalScroll)

    def _append(self, text: str, role: str, log: bool = True) -> Static:
        widget = Static(text, classes=f"msg-{role}")
        if role in self.HIDDEN_KINDS:
            widget.styles.display = "block" if self.show_thinking else "none"
        if log:
            self.ui_lines.append((role, text))
        self._history().mount(widget)
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
            self._send_chat(parsed.text.strip())
        else:
            self._handle_command(parsed.name)

    def _roll_context(self, need: int, limit: int) -> int:
        """Drop oldest turns (keeping the latest exchange) until the budget fits."""
        dropped = 0
        while len(self.chat) > 2 and need > limit:
            removed = self.chat.pop(0)
            need -= estimate_tokens(removed["content"])
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
        older, newer = self.chat[:tail_start], self.chat[tail_start:]
        if not older:  # nothing worth summarizing; just proceed
            self._stop_thinking()
            self.busy = False
            self._send_chat(text)
            return
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in older)
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
        self.chat[:] = [
            {"role": "system", "content": "[Summary of earlier conversation]\n" + resp.content.strip()}
        ] + newer
        self.context_used = 0
        self._stop_thinking()
        self.busy = False
        self._set_notice(f"compacted {len(older)} message(s) into summary")
        self._update_ctx_label()
        self._send_chat(text)

    def _send_chat(self, text: str) -> None:
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

    def _knowledge_tools(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Build tool schemas from enabled knowledge entries.

        Returns (schemas, {tool_name: resolved_path}). Security: the model can
        only reach files already listed (and enabled) in the config - tools
        take no arguments, so it can never name a file to read itself.
        """
        schemas: list[dict[str, Any]] = []
        files: dict[str, str] = {}
        used: set[str] = set()
        for i, entry in enumerate(self.config.get("knowledge", [])):
            if not entry.get("enabled"):
                continue
            path = os.path.abspath(os.path.expanduser(entry["file"]))
            stem = re.sub(r"\W+", "_", Path(entry["file"]).stem).strip("_") or "knowledge"
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

    @work(exclusive=True, group="chat")
    async def _chat_worker(self) -> None:
        cfg = self.config.values
        messages: list[dict[str, Any]] = []
        system_prompt = (cfg.get("system_prompt") or "").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self.chat)
        tools, tool_files = self._knowledge_tools()
        rounds = 0
        total_completion = 0
        last_prompt: Optional[int] = None
        last_elapsed = 0.0
        try:
            while True:
                resp = await asyncio.to_thread(
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
                )
                if resp.prompt_tokens is not None:
                    last_prompt = resp.prompt_tokens
                total_completion += resp.completion_tokens or 0
                last_elapsed += resp.elapsed
                if resp.reasoning.strip():
                    self._append(
                        f"{self.config['labels']['assistant']} thinking:\n{resp.reasoning.strip()}",
                        "reasoning",
                    )
                if resp.tool_calls and tool_files and rounds < self.KNOWLEDGE_TOOL_ROUNDS:
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
            self.chat.pop()
            self._update_ctx_label()
            return

        self._stop_thinking()
        self.busy = False
        if last_prompt is not None:
            self.context_used = last_prompt + total_completion
        self._update_ctx_label()
        self.chat.append({"role": "assistant", "content": resp.content})
        self.full_chat.append({"role": "assistant", "content": resp.content})
        assistant_label = self.config["labels"]["assistant"]
        # models often pad replies with blank lines; don't render them
        shown = resp.content.strip("\n") if resp.content.strip() else resp.content
        self._append(f"{assistant_label}:\n{shown}", "assistant")
        self._append(format_stats(last_elapsed, total_completion), "stats")
        if self.config.get("autosave") and self.full_chat:
            path = self._save_history()
            self._append(f"(autosaved to {path})", "stats")
        self._history().scroll_end(animate=False)

    # --------------------------------------------------------------- commands

    def _handle_command(self, name: str) -> None:
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
                "  escape    clear the input box",
                "(key bindings and colors are configured in the config file)",
            ]
            self._sys("\n".join(lines))
        elif command == "save":
            self._confirm_save()
        elif command == "config":
            self.push_screen(ConfigScreen(self.config), self._apply_config)
        elif command == "clear":
            for child in list(self._history().children):
                child.remove()
            self.chat.clear()
            self.full_chat.clear()
            self.ui_lines.clear()
            self._set_notice("")
            self._sys("(history cleared)")
        elif command == "models":
            self._ensure_models(refresh=True)
        elif command == "knowledge":
            self.push_screen(KnowledgeScreen(self.config))
        elif command == "thinking":
            self.action_toggle_thinking()
        elif command == "exit":
            self.exit()

    def _confirm_save(self) -> None:
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

    def _save_history(self) -> Optional[str]:
        try:
            path = storage.save_history(
                self.full_chat,
                directory=self.config.get("save_dir", "."),
                fmt=self.config.get("save_format", "json"),
            )
        except OSError as exc:
            self._sys(f"Could not save: {exc}", error=True)
            return None
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
        self.config.values.update(values)
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
            messages = storage.load_history(path)
        except storage.StorageError as exc:
            self._sys(f"Could not resume {path}: {exc}", error=True)
            return
        self.chat = copy.deepcopy(messages)
        self.full_chat = copy.deepcopy(messages)
        labels = self.config["labels"]
        for msg in messages:
            role, content = msg["role"], msg["content"]
            if role == "user":
                self._append(f"{labels['user']}:\n{content}", "user")
            elif role == "assistant":
                self._append(f"{labels['assistant']}:\n{content}", "assistant")
            else:
                self._append(f"(restored context note)\n{content}", "system")
        self._sys(f"(resumed {len(messages)} messages from {path})")
        self._update_ctx_label()

    def _open_resume_picker(self) -> None:
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
            self._select_model(chosen)
        elif refresh:
            self._sys("(kept current model)")

    def _select_model(self, model: str) -> None:
        self.config["model"] = model
        self.title = f"sekka - {model}"
        self._apply_context_total()
        self._sys(f"Model selected: {model}")

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
