"""Configuration handling for Sekka.

Precedence (highest wins): CLI flags > environment variables > config file > defaults.

The config file is searched for in this order:
    1. --config PATH (or SEKKA_CONFIG env var)
    2. ./.sekka/config.json   (current working directory)
    3. ~/.sekka/config.json   (user home directory)
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Optional

from rich.color import Color, ColorParseError

ENV_ENDPOINT = "SEKKA_ENDPOINT"
ENV_MODEL = "SEKKA_MODEL"
ENV_API_KEY = "SEKKA_API_KEY"
ENV_CONFIG = "SEKKA_CONFIG"
ENV_CAMPAIGN = "SEKKA_CAMPAIGN"

DEFAULT_CONFIG: dict[str, Any] = {
    "endpoint": "http://localhost:8000/v1",
    "model": "",
    "api_key": "",
    "system_prompt": "You are a helpful assistant.",
    "temperature": 0.7,
    "max_tokens": None,
    "top_p": None,
    "min_p": None,
    "presence_penalty": None,
    "frequency_penalty": None,
    "repetition_penalty": None,
    "stop": [],
    "request_timeout": 300,
    "history_percent": 80,
    "context_window": None,
    "context_mode": "pause",
    "reasoning": "medium",
    "stream": True,
    "knowledge": [],
    "autosave": False,
    "remember": True,
    "readonly": False,
    "serve_host": "127.0.0.1",
    "serve_port": 8484,
    "save_dir": ".",
    "save_format": "json",
    "labels": {"user": "You", "assistant": "Assistant"},
    "greeting": "",
    "player": "",
    "note": "",
    "campaign": "",
    "theme": {
        "user": "cyan",
        "assistant": "magenta",
        "system": "yellow",
        "stats": "grey58",
        "error": "red",
    },
    "keys": {
        "submit": "enter",
        "newline": "alt+enter",
        "scroll_up": "pageup",
        "scroll_down": "pagedown",
    },
}

MAX_LORE_KEYWORDS = 20
MAX_LORE_KEYWORD_CHARS = 64


def _validate_knowledge_entries(entries: Any, where: str) -> None:
    """Shared shape check for knowledge entries (config file and campaign file)."""
    if not isinstance(entries, list):
        raise ConfigError(f"{where} must be a list of entries.")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("file"), str)
            or not entry["file"].strip()
            or not isinstance(entry.get("description"), str)
            or not isinstance(entry.get("enabled"), bool)
        ):
            raise ConfigError(
                f"Each {where} entry needs a non-empty 'file' (str), "
                "a 'description' (str) and an 'enabled' (bool)."
            )
        if not isinstance(entry.get("always", False), bool):
            raise ConfigError(f"'always' in a {where} entry must be true or false.")
        keywords = entry.get("keywords", [])
        if not isinstance(keywords, list) or len(keywords) > MAX_LORE_KEYWORDS:
            raise ConfigError(
                f"'keywords' in a {where} entry must be a list of up to "
                f"{MAX_LORE_KEYWORDS} strings."
            )
        for keyword in keywords:
            if (
                not isinstance(keyword, str)
                or not keyword.strip()
                or len(keyword) > MAX_LORE_KEYWORD_CHARS
            ):
                raise ConfigError(
                    f"Each 'keywords' item in a {where} entry must be a short non-empty string."
                )


SAMPLER_RANGES = {
    "top_p": (0.0, 1.0),
    "min_p": (0.0, 1.0),
    "presence_penalty": (-2.0, 2.0),
    "frequency_penalty": (-2.0, 2.0),
    "repetition_penalty": (0.0, 2.0),
}
MAX_STOP_SEQUENCES = 8
MAX_STOP_CHARS = 512

VALID_SAVE_FORMATS = {"json", "markdown"}
VALID_CONTEXT_MODES = {"pause", "rolling", "compact"}
VALID_REASONING = {"none", "minimal", "low", "medium", "high"}


class ConfigError(Exception):
    """Raised when configuration is invalid or unreadable."""


class Config:
    """Thin wrapper around the resolved configuration values."""

    def __init__(
        self,
        values: dict[str, Any],
        path: Optional[Path] = None,
        cli_keys: Optional[set[str]] = None,
        file_values: Optional[dict[str, Any]] = None,
        env_keys: Optional[set[str]] = None,
        campaign_path: Optional[Path] = None,
        campaign_keys: Optional[set[str]] = None,
    ) -> None:
        self.values = values
        self.path = path
        self.cli_keys = cli_keys or set()
        self.env_keys = env_keys or set()
        # what the config file itself contained (so overrides never get persisted)
        self.file_values = file_values or {}
        self.campaign_path = campaign_path
        self.campaign_keys = campaign_keys or set()

    @property
    def overridden(self) -> set[str]:
        """Keys whose value came from CLI/env/campaign, not from the config file."""
        return self.cli_keys | self.env_keys | self.campaign_keys

    @property
    def base_dir(self) -> Path:
        """What relative paths (knowledge files) are resolved against."""
        if self.campaign_path is not None:
            return self.campaign_path.parent
        if self.path is not None:
            return self.path.parent
        return Path.cwd()

    def resolve_path(self, raw: str) -> Path:
        """Absolute paths pass through; relative ones try base dir, then cwd.

        Campaigns/configs shipped as a folder therefore keep working no matter
        where sekka is launched from, while configs written for the older
        "always cd into the folder" convention still resolve too.
        """
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
        for base in (self.base_dir, Path.cwd()):
            candidate = base / p
            if candidate.is_file():
                return candidate
        return Path.cwd() / p

    def release_override(self, key: str) -> None:
        """The user explicitly set ``key`` in the UI: it may be persisted now."""
        self.cli_keys.discard(key)
        self.env_keys.discard(key)
        self.campaign_keys.discard(key)

    def persistable(self) -> dict[str, Any]:
        """Values to write to disk: CLI/env overrides keep their file value
        (or are omitted when the file never had one), so secrets such as
        SEKKA_API_KEY are never written by accident."""
        out = copy.deepcopy(self.values)
        for key in self.overridden:
            if key in self.file_values:
                out[key] = copy.deepcopy(self.file_values[key])
            else:
                out.pop(key, None)
        return out

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        return self.values.setdefault(key, default)

    @property
    def theme(self) -> dict[str, str]:
        return self.values["theme"]

    @property
    def keys(self) -> dict[str, str]:
        return self.values["keys"]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.values)


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` (returns a new dict)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_config(values: dict[str, Any]) -> None:
    """Raise ConfigError when configuration values are unusable."""
    endpoint = values.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ConfigError("Config 'endpoint' must be a non-empty string.")

    model = values.get("model")
    if not isinstance(model, str):
        raise ConfigError("Config 'model' must be a string.")

    for name in ("user", "assistant", "system", "stats", "error"):
        color = values.get("theme", {}).get(name)
        try:
            Color.parse(str(color))
        except (ColorParseError, AttributeError):
            raise ConfigError(
                f"Config theme color '{name}' is not a valid color: {color!r}"
            )

    if values.get("save_format") not in VALID_SAVE_FORMATS:
        raise ConfigError(
            f"Config 'save_format' must be one of {sorted(VALID_SAVE_FORMATS)}."
        )

    for name, (low, high) in SAMPLER_RANGES.items():
        value = values.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"Config '{name}' must be a number or null.")
        if not low <= float(value) <= high:
            raise ConfigError(f"Config '{name}' must be between {low} and {high}.")

    stops = values.get("stop")
    if not isinstance(stops, list) or len(stops) > MAX_STOP_SEQUENCES:
        raise ConfigError(
            f"Config 'stop' must be a list of up to {MAX_STOP_SEQUENCES} strings."
        )
    for sequence in stops:
        if not isinstance(sequence, str) or not sequence or len(sequence) > MAX_STOP_CHARS:
            raise ConfigError(
                f"Each 'stop' entry must be a non-empty string of at most {MAX_STOP_CHARS} chars."
            )

    percent = values.get("history_percent")
    if not isinstance(percent, int) or isinstance(percent, bool) or not 50 <= percent <= 95:
        raise ConfigError("Config 'history_percent' must be an integer between 50 and 95.")

    window = values.get("context_window")
    if window is not None and (not isinstance(window, int) or isinstance(window, bool) or window < 1024):
        raise ConfigError("Config 'context_window' must be null or an integer of at least 1024.")

    if values.get("context_mode") not in VALID_CONTEXT_MODES:
        raise ConfigError(
            f"Config 'context_mode' must be one of {sorted(VALID_CONTEXT_MODES)}."
        )

    timeout = values.get("request_timeout")
    if timeout is not None and (
        not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0
    ):
        raise ConfigError("Config 'request_timeout' must be null, 0 (wait forever), or a positive number.")

    host = values.get("serve_host")
    if not isinstance(host, str) or not host.strip():
        raise ConfigError("Config 'serve_host' must be a non-empty host or address.")

    port = values.get("serve_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError("Config 'serve_port' must be an integer port between 1 and 65535.")

    if not isinstance(values.get("readonly"), bool):
        raise ConfigError("Config 'readonly' must be true or false.")

    if not isinstance(values.get("remember"), bool):
        raise ConfigError("Config 'remember' must be true or false.")

    if not isinstance(values.get("stream"), bool):
        raise ConfigError("Config 'stream' must be true or false.")

    if values.get("reasoning") not in VALID_REASONING:
        raise ConfigError(
            f"Config 'reasoning' must be one of {sorted(VALID_REASONING)}."
        )

    _validate_knowledge_entries(values.get("knowledge"), "config 'knowledge'")

    note = values.get("note", "")
    if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
        raise ConfigError(
            f"Config 'note' must be a string of at most {MAX_NOTE_CHARS} characters."
        )

    labels = values.get("labels", {})
    for name in ("user", "assistant"):
        label = labels.get(name)
        if not isinstance(label, str) or not label.strip() or len(label) > 30:
            raise ConfigError(
                f"Config label '{name}' must be a non-empty string of at most 30 characters."
            )

    for name, binding in values.get("keys", {}).items():
        if not isinstance(binding, str) or not binding.strip():
            raise ConfigError(f"Config key binding '{name}' must be a non-empty string.")


CAMPAIGN_KEYS = {
    "name", "system_prompt", "labels", "knowledge", "temperature", "max_tokens",
    "reasoning", "greeting", "player", "note",
    "top_p", "min_p", "presence_penalty", "frequency_penalty", "repetition_penalty", "stop",
}

MAX_NOTE_CHARS = 20_000


def validate_campaign(values: dict[str, Any]) -> None:
    """Raise ConfigError for a malformed campaign file."""
    if not isinstance(values, dict):
        raise ConfigError("Campaign file must contain a JSON object.")
    for key in ("name", "system_prompt", "greeting", "player", "note"):
        if key in values and not isinstance(values[key], str):
            raise ConfigError(f"Campaign '{key}' must be a string.")
    if "temperature" in values and values["temperature"] is not None and (
        not isinstance(values["temperature"], (int, float))
        or isinstance(values["temperature"], bool)
    ):
        raise ConfigError("Campaign 'temperature' must be a number or null.")
    if "max_tokens" in values and values["max_tokens"] is not None and (
        not isinstance(values["max_tokens"], int) or isinstance(values["max_tokens"], bool)
    ):
        raise ConfigError("Campaign 'max_tokens' must be an integer or null.")
    if "note" in values and isinstance(values["note"], str) and len(values["note"]) > MAX_NOTE_CHARS:
        raise ConfigError(f"Campaign 'note' must be at most {MAX_NOTE_CHARS} characters.")
    for name in SAMPLER_RANGES:
        if name in values and values[name] is not None and (
            isinstance(values[name], bool) or not isinstance(values[name], (int, float))
        ):
            raise ConfigError(f"Campaign '{name}' must be a number or null.")
    if "stop" in values and (
        not isinstance(values["stop"], list)
        or any(not isinstance(x, str) or not x for x in values["stop"])
    ):
        raise ConfigError("Campaign 'stop' must be a list of non-empty strings.")
    if "reasoning" in values and values["reasoning"] not in VALID_REASONING:
        raise ConfigError(f"Campaign 'reasoning' must be one of {sorted(VALID_REASONING)}.")
    if "labels" in values:
        labels = values["labels"]
        if not isinstance(labels, dict):
            raise ConfigError("Campaign 'labels' must be an object.")
        for name in ("user", "assistant"):
            if name in labels and (
                not isinstance(labels[name], str) or not labels[name].strip()
                or len(labels[name]) > 30
            ):
                raise ConfigError(f"Campaign label '{name}' must be a short non-empty string.")
    if "knowledge" in values:
        _validate_knowledge_entries(values["knowledge"], "campaign 'knowledge'")


def _load_campaign(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Campaign file {path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read campaign file {path}: {exc}") from exc
    validate_campaign(data)
    return {k: v for k, v in data.items() if k in CAMPAIGN_KEYS}


def find_campaign(
    explicit: Optional[str] = None,
    config_path: Optional[Path] = None,
    file_values: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Which campaign file to use, or None.

    Order: --campaign / SEKKA_CAMPAIGN, the 'campaign' key in the config file
    (relative to that file), ``campaign.json`` next to the config, then
    ``./.sekka/campaign.json``.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Campaign file not found: {path}")
        return path
    env = os.environ.get(ENV_CAMPAIGN)
    if env:
        path = Path(env).expanduser()
        if not path.is_file():
            raise ConfigError(f"Campaign file not found (from {ENV_CAMPAIGN}): {path}")
        return path
    base = config_path.parent if config_path else Path.cwd()
    named = (file_values or {}).get("campaign")
    if isinstance(named, str) and named.strip():
        path = Path(named).expanduser()
        if not path.is_absolute() and config_path:
            path = config_path.parent / path
        if path.is_file():
            return path
        raise ConfigError(f"Campaign file from config not found: {path}")
    for candidate in (base / "campaign.json", Path.cwd() / ".sekka" / "campaign.json"):
        if candidate.is_file():
            return candidate
    return None


def find_config_file(explicit: Optional[str] = None) -> Optional[Path]:
    """Return the config file path to use, or None when no file exists."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    candidates = [
        Path.cwd() / ".sekka" / "config.json",
        Path.home() / ".sekka" / "config.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def default_save_path() -> Path:
    """Where to write config when no config file existed: ./.sekka/config.json"""
    return Path.cwd() / ".sekka" / "config.json"


def load_config(
    cli_overrides: Optional[dict[str, Any]] = None,
    config_path: Optional[str] = None,
    campaign_path: Optional[str] = None,
) -> Config:
    """Build the effective configuration from all layers.

    Precedence: CLI flags > environment > campaign file > config file > defaults,
    so the same campaign can be pointed at another endpoint from the shell.
    """
    values = copy.deepcopy(DEFAULT_CONFIG)

    path = find_config_file(config_path)
    file_values: dict[str, Any] = {}
    if path is not None:
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        try:
            file_values = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file {path} is not valid JSON: {exc}") from exc
        if not isinstance(file_values, dict):
            raise ConfigError(f"Config file {path} must contain a JSON object.")
        values = deep_merge(values, file_values)

    campaign = find_campaign(campaign_path, path, file_values)
    campaign_values: dict[str, Any] = {}
    if campaign is not None:
        campaign_values = _load_campaign(campaign)
        values = deep_merge(values, campaign_values)

    env_overrides = {}
    if os.environ.get(ENV_ENDPOINT):
        env_overrides["endpoint"] = os.environ[ENV_ENDPOINT]
    if os.environ.get(ENV_MODEL):
        env_overrides["model"] = os.environ[ENV_MODEL]
    if os.environ.get(ENV_API_KEY):
        env_overrides["api_key"] = os.environ[ENV_API_KEY]
    values = deep_merge(values, env_overrides)
    env_keys = set(env_overrides)

    cli_keys: set[str] = set()
    if cli_overrides:
        cli_keys = {k for k, v in cli_overrides.items() if v is not None}
        clean = {k: v for k, v in cli_overrides.items() if v is not None}
        values = deep_merge(values, clean)

    try:
        validate_config(values)
    except ConfigError as exc:
        raise ConfigError(f"{exc}") from None

    return Config(
        values,
        path=path,
        cli_keys=cli_keys,
        file_values=file_values,
        env_keys=env_keys,
        campaign_path=campaign,
        campaign_keys=set(campaign_values) - cli_keys - env_keys,
    )


# What `sekka --endpoint URL` is allowed to write into the config file so the
# next bare `sekka` in this directory just works. Endpoint and model are the two
# things every first run needs, and neither is a secret. api_key deliberately is
# not on this list: it stays in the flag/env/campaign or in a file you wrote.
REMEMBERED_KEYS = ("endpoint", "model")


def remember_cli_values(config: Config) -> tuple[Optional[Path], list[str]]:
    """Persist the safe CLI overrides into the config file.

    Returns ``(path_written_or_None, keys_remembered)``. Values that came from
    the environment, or that the config file already has identical, are not
    written; nothing is written at all when `remember` is off.
    """
    if not config.values.get("remember", True):
        return None, []
    keys = [
        key
        for key in REMEMBERED_KEYS
        if key in config.cli_keys
        and key not in config.env_keys
        and config.values.get(key) not in (None, "")
    ]
    if not keys:
        return None, []

    # Always remember *locally*: if the only config found was ~/.sekka/config.json,
    # writing a project's endpoint into the user's global file would leak it into
    # every other directory. The local file wins over the user file, as it should.
    target = config.path
    if target is None or _under_cwd(target) is False:
        target = default_save_path()

    wanted = {key: copy.deepcopy(config.values[key]) for key in keys}
    try:
        current = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    except (OSError, ValueError):
        current = {}
    if isinstance(current, dict) and all(
        current.get(key) == value for key, value in wanted.items()
    ):
        return None, []            # already recorded: leave the file alone

    for key in wanted:
        config.release_override(key)          # the user asked for this: persist it
    config.path = target                      # /config edits this file from now on
    path = save_config(config)
    return path, list(wanted)


def _under_cwd(path: Path) -> Optional[bool]:
    """True when path sits inside the working directory, False when it clearly
    does not, None when it cannot be decided."""
    try:
        path.resolve().relative_to(Path.cwd().resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return None


def save_campaign_values(config: Config, values: dict[str, Any]) -> Optional[Path]:
    """Merge ``values`` into the loaded campaign file (read-modify-write).

    /config and /note change campaign-owned values; writing them to config.json
    would be ignored next start (the campaign layer overrides it), so they are
    written back into the campaign itself. Keys the file has that we don't touch
    are preserved.
    """
    if config.campaign_path is None or not values:
        return None
    path = config.campaign_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Could not update campaign file {path}: {exc}") from exc
    data.update({k: copy.deepcopy(v) for k, v in values.items() if k in CAMPAIGN_KEYS})
    if isinstance(data.get("note"), str) and len(data["note"]) > MAX_NOTE_CHARS:
        raise ConfigError(f"Campaign 'note' must be at most {MAX_NOTE_CHARS} characters.")
    try:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not update campaign file {path}: {exc}") from exc
    config.values.update(data)
    config.campaign_keys |= set(values)
    return path


def save_config(config: Config, path: Optional[Path] = None) -> Path:
    """Persist the full configuration to disk. Returns the path written."""
    target = Path(path) if path else (config.path or default_save_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config.persistable(), indent=2) + "\n")
    config.path = target
    return target
