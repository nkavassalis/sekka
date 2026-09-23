"""The Sekka terminal UI (built on Textual).

Layout: chat history takes the top of the screen (config.history_percent,
default 80) and the multi-line input editor the remainder. Arrow keys move
inside the input editor; PageUp/PageDown scroll the history (both
configurable).
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from rich.color import Color
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, ListItem, ListView, Static, TextArea

from . import client, commands, storage
from .config import DEFAULT_CONFIG, Config, save_config, validate_config, ConfigError
from .stats import format_stats


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
                *[ListItem(Label(name, id=f"model:{name}")) for name in self.models],
                id="model_list",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        label = event.item.children[0] if event.item.children else None
        if label is not None and label.id and label.id.startswith("model:"):
            self.dismiss(label.id[len("model:"):])
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

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    DEFAULT_CSS = """
    ConfigScreen { align: center middle; }
    ConfigScreen > Vertical {
        width: 80%; max-width: 100; height: auto; max-height: 95%;
        padding: 1 2; background: $surface; border: thick $primary;
    }
    ConfigScreen Label { padding-top: 1; color: $primary; }
    ConfigScreen TextArea { height: 6; border: round $primary 40%; }
    ConfigScreen Input { border: round $primary 40%; }
    #config_buttons { align-horizontal: right; }
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
            yield Static("Sekka configuration (esc cancels)", classes="msg-system")
            yield Label("Endpoint")
            yield Input(value=str(values.get("endpoint", "")), id="cfg_endpoint")
            yield Label("Model (leave empty to pick from endpoint)")
            yield Input(value=str(values.get("model", "")), id="cfg_model")
            yield Label("API key (optional)")
            yield Input(value=str(values.get("api_key", "")), password=True, id="cfg_api_key")
            yield Label("System prompt")
            yield TextArea(str(values.get("system_prompt", "")), id="cfg_system")
            yield Label("Temperature (blank = endpoint default)")
            yield Input(value=_blank_if_none(values.get("temperature")), id="cfg_temperature")
            yield Label("Max tokens (blank = endpoint default)")
            yield Input(value=_blank_if_none(values.get("max_tokens")), id="cfg_max_tokens")
            yield Label("Save directory")
            yield Input(value=str(values.get("save_dir", ".")), id="cfg_save_dir")
            yield Checkbox("Autosave history after every reply", value=bool(values.get("autosave")), id="cfg_autosave")
            with Vertical(id="config_buttons"):
                yield Button("Save", id="config_save", variant="primary")
                yield Button("Cancel", id="config_cancel", variant="default")

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
        except _ConfigInputError:
            return

        self.dismiss(
            {
                "endpoint": endpoint,
                "model": self.query_one("#cfg_model", Input).value.strip(),
                "api_key": self.query_one("#cfg_api_key", Input).value,
                "system_prompt": self.query_one("#cfg_system", TextArea).text,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "save_dir": self.query_one("#cfg_save_dir", Input).value.strip() or ".",
                "autosave": bool(self.query_one("#cfg_autosave", Checkbox).value),
            }
        )


class _ConfigInputError(Exception):
    pass


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
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        super().__init__()
        self.chat: list[dict[str, str]] = []  # user/assistant history (no system)
        self.busy = False
        self._thinking: Optional[Static] = None
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

    # ------------------------------------------------------------- ui helpers

    def _history(self) -> VerticalScroll:
        return self.query_one("#history", VerticalScroll)

    def _append(self, text: str, role: str) -> Static:
        widget = Static(text, classes=f"msg-{role}")
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

    def _send_chat(self, text: str) -> None:
        if self.busy:
            self.notify("Still waiting for the current reply.", severity="warning")
            return
        model = self.config["model"]
        if not model:
            self._sys("No model selected. Use /models or /config first.", error=True)
            return
        self.chat.append({"role": "user", "content": text})
        self._append(f"You:\n{text}", "user")
        self.busy = True
        self._thinking = self._append("\u2581 thinking\u2026", "stats")
        self._chat_worker()

    @work(exclusive=True, group="chat")
    async def _chat_worker(self) -> None:
        cfg = self.config.values
        messages: list[dict[str, str]] = []
        system_prompt = (cfg.get("system_prompt") or "").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self.chat)
        try:
            resp = await asyncio.to_thread(
                client.chat_completion,
                cfg["endpoint"],
                cfg["model"],
                messages,
                api_key=cfg.get("api_key", ""),
                temperature=cfg.get("temperature"),
                max_tokens=cfg.get("max_tokens"),
                timeout=float(cfg.get("request_timeout", 120)),
            )
        except client.ClientError as exc:
            self._thinking.remove()
            self.busy = False
            self._sys(f"Error: {exc}", error=True)
            # roll back the unanswered user turn so history stays consistent
            self.chat.pop()
            return

        self._thinking.remove()
        self.busy = False
        self.chat.append({"role": "assistant", "content": resp.content})
        self._append(f"Assistant:\n{resp.content}", "assistant")
        self._append(format_stats(resp.elapsed, resp.completion_tokens), "stats")
        if self.config.get("autosave") and self.chat:
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
                "  ctrl+q    quit",
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
            self.ui_lines.clear()
            self._sys("(history cleared)")
        elif command == "models":
            self._ensure_models(refresh=True)
        elif command == "exit":
            self.exit()

    def _confirm_save(self) -> None:
        if not self.chat:
            self._sys("Nothing to save.")
            return
        name = storage.timestamp_name(fmt=self.config.get("save_format", "json"))

        def done(confirmed: bool) -> None:
            if not confirmed:
                self._sys("(save cancelled)")
                return
            path = self._save_history()
            if path:
                self._sys(f"Saved {len(self.chat)} messages to {path}")

        self.push_screen(ConfirmScreen(f"Save chat history as {name}?"), done)

    def _save_history(self) -> Optional[str]:
        try:
            path = storage.save_history(
                self.chat,
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
        self.query_one("#input", ChatInput).set_keymap(self.config.keys)
        self.refresh_css()

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
        if not models:
            self._sys("Endpoint reported no models. Set one with --model or /config.", error=True)
            return
        if len(models) == 1:
            self._select_model(models[0])
            return
        chosen = await self.push_screen_wait(ModelScreen(models))
        if chosen:
            self._select_model(chosen)
        elif refresh:
            self._sys("(kept current model)")

    def _select_model(self, model: str) -> None:
        self.config["model"] = model
        self.title = f"sekka - {model}"
        self._sys(f"Model selected: {model}")


def _blank_if_none(value: Any) -> str:
    return "" if value is None else str(value)
