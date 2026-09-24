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

DEFAULT_CONFIG: dict[str, Any] = {
    "endpoint": "http://localhost:8000/v1",
    "model": "",
    "api_key": "",
    "system_prompt": "You are a helpful assistant.",
    "temperature": 0.7,
    "max_tokens": None,
    "request_timeout": 120,
    "history_percent": 80,
    "context_window": None,
    "context_mode": "pause",
    "autosave": False,
    "save_dir": ".",
    "save_format": "json",
    "labels": {"user": "You", "assistant": "Assistant"},
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

VALID_SAVE_FORMATS = {"json", "markdown"}
VALID_CONTEXT_MODES = {"pause", "rolling", "compact"}


class ConfigError(Exception):
    """Raised when configuration is invalid or unreadable."""


class Config:
    """Thin wrapper around the resolved configuration values."""

    def __init__(
        self,
        values: dict[str, Any],
        path: Optional[Path] = None,
        cli_keys: Optional[set[str]] = None,
    ) -> None:
        self.values = values
        self.path = path
        self.cli_keys = cli_keys or set()

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

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
) -> Config:
    """Build the effective configuration from all layers."""
    values = copy.deepcopy(DEFAULT_CONFIG)

    path = find_config_file(config_path)
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

    env_overrides = {}
    if os.environ.get(ENV_ENDPOINT):
        env_overrides["endpoint"] = os.environ[ENV_ENDPOINT]
    if os.environ.get(ENV_MODEL):
        env_overrides["model"] = os.environ[ENV_MODEL]
    if os.environ.get(ENV_API_KEY):
        env_overrides["api_key"] = os.environ[ENV_API_KEY]
    values = deep_merge(values, env_overrides)

    cli_keys: set[str] = set()
    if cli_overrides:
        cli_keys = {k for k, v in cli_overrides.items() if v is not None}
        clean = {k: v for k, v in cli_overrides.items() if v is not None}
        values = deep_merge(values, clean)

    try:
        validate_config(values)
    except ConfigError as exc:
        raise ConfigError(f"{exc}") from None

    return Config(values, path=path, cli_keys=cli_keys)


def save_config(config: Config, path: Optional[Path] = None) -> Path:
    """Persist the full configuration to disk. Returns the path written."""
    target = Path(path) if path else (config.path or default_save_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config.to_dict(), indent=2) + "\n")
    config.path = target
    return target
