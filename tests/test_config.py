import json
from pathlib import Path

import pytest

from sekka.config import (
    DEFAULT_CONFIG,
    Config,
    ConfigError,
    deep_merge,
    load_config,
    save_config,
    validate_config,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate config discovery from the developer's machine."""
    for var in ("SEKKA_ENDPOINT", "SEKKA_MODEL", "SEKKA_API_KEY", "SEKKA_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return tmp_path


def make_config_file(path: Path, values: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


def test_defaults_when_no_file():
    cfg = load_config()
    assert cfg["endpoint"] == DEFAULT_CONFIG["endpoint"]
    assert cfg["model"] == ""
    assert cfg["autosave"] is False
    assert cfg["keys"]["submit"] == "enter"
    assert cfg["theme"]["user"] == "cyan"


def test_file_values_are_merged(clean_env):
    make_config_file(Path(".sekka/config.json"), {
        "endpoint": "http://example.invalid/v1",
        "model": "llama-3",
        "theme": {"user": "green"},
    })
    cfg = load_config()
    assert cfg["endpoint"] == "http://example.invalid/v1"
    assert cfg["model"] == "llama-3"
    assert cfg["theme"]["user"] == "green"
    # untouched keys keep defaults
    assert cfg["theme"]["assistant"] == DEFAULT_CONFIG["theme"]["assistant"]
    assert cfg["keys"] == DEFAULT_CONFIG["keys"]


def test_home_file_used_when_no_cwd_file(clean_env):
    make_config_file(clean_env / "home/.sekka/config.json", {"model": "home-model"})
    cfg = load_config()
    assert cfg["model"] == "home-model"


def test_cwd_file_wins_over_home(clean_env):
    make_config_file(clean_env / "home/.sekka/config.json", {"model": "home-model"})
    make_config_file(Path(".sekka/config.json"), {"model": "cwd-model"})
    cfg = load_config()
    assert cfg["model"] == "cwd-model"


def test_cli_overrides_file_and_env(clean_env, monkeypatch):
    monkeypatch.setenv("SEKKA_MODEL", "env-model")
    monkeypatch.setenv("SEKKA_ENDPOINT", "http://env.invalid/v1")
    make_config_file(Path(".sekka/config.json"), {"model": "file-model", "endpoint": "http://file.invalid/v1"})
    cfg = load_config({"model": "cli-model"})
    assert cfg["model"] == "cli-model"        # CLI wins
    assert cfg["endpoint"] == "http://env.invalid/v1"  # env beats file


def test_none_cli_values_are_ignored(clean_env):
    cfg = load_config({"model": None, "endpoint": None})
    assert cfg["model"] == ""
    assert cfg["endpoint"] == DEFAULT_CONFIG["endpoint"]


def test_explicit_missing_config_raises(clean_env):
    with pytest.raises(ConfigError, match="not found"):
        load_config(config_path="nope/config.json")


def test_invalid_json_raises(clean_env):
    make_config_file(Path(".sekka/config.json"), {})
    Path(".sekka/config.json").write_text("{not json")
    with pytest.raises(ConfigError, match="valid JSON"):
        load_config()


def test_invalid_color_raises(clean_env):
    bad = json.loads(json.dumps(DEFAULT_CONFIG))
    bad["theme"]["user"] = "notacolor"
    with pytest.raises(ConfigError, match="theme color"):
        validate_config(bad)


def test_invalid_save_format_raises():
    bad = json.loads(json.dumps(DEFAULT_CONFIG))
    bad["save_format"] = "docx"
    with pytest.raises(ConfigError, match="save_format"):
        validate_config(bad)


def test_context_settings_defaults_and_validation():
    assert DEFAULT_CONFIG["context_window"] is None
    assert DEFAULT_CONFIG["context_mode"] == "pause"
    assert DEFAULT_CONFIG["request_timeout"] == 300
    for bad_timeout in (-5, "soon", True):
        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["request_timeout"] = bad_timeout
        with pytest.raises(ConfigError, match="request_timeout"):
            validate_config(bad)
    for good_timeout in (None, 0, 600):
        good = json.loads(json.dumps(DEFAULT_CONFIG))
        good["request_timeout"] = good_timeout
        validate_config(good)
    for bad_mode in ("shuffle", "", None, 5):
        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["context_mode"] = bad_mode
        with pytest.raises(ConfigError, match="context_mode"):
            validate_config(bad)
    for bad_window in (999, "big", True):
        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["context_window"] = bad_window
        with pytest.raises(ConfigError, match="context_window"):
            validate_config(bad)
    good = json.loads(json.dumps(DEFAULT_CONFIG))
    good["context_window"] = 131072
    good["context_mode"] = "rolling"
    validate_config(good)


def test_labels_defaults_and_validation():
    assert DEFAULT_CONFIG["labels"] == {"user": "You", "assistant": "Assistant"}
    for bad in ("", "   ", "x" * 31, 42, None):
        bad_values = json.loads(json.dumps(DEFAULT_CONFIG))
        bad_values["labels"]["user"] = bad
        with pytest.raises(ConfigError, match="label"):
            validate_config(bad_values)
    good = json.loads(json.dumps(DEFAULT_CONFIG))
    good["labels"] = {"user": "Nick", "assistant": "Qwen"}
    validate_config(good)


def test_history_percent_bounds():
    assert DEFAULT_CONFIG["history_percent"] == 80
    for bad_value in (49, 96, "80", None, 80.5, True):
        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["history_percent"] = bad_value
        with pytest.raises(ConfigError, match="history_percent"):
            validate_config(bad)
    for good in (50, 80, 95):
        good_values = json.loads(json.dumps(DEFAULT_CONFIG))
        good_values["history_percent"] = good
        validate_config(good_values)


def test_deep_merge_is_not_destructive():
    base = {"a": {"b": 1}, "c": [1, 2]}
    override = {"a": {"d": 2}}
    merged = deep_merge(base, override)
    assert merged == {"a": {"b": 1, "d": 2}, "c": [1, 2]}
    assert base == {"a": {"b": 1}, "c": [1, 2]}
    merged["c"].append(3)
    assert base["c"] == [1, 2]


def test_save_and_reload_roundtrip(clean_env):
    cfg = load_config({"model": "roundtrip"})
    written = save_config(cfg)
    assert written == Path.cwd() / ".sekka" / "config.json"
    assert written.is_file()
    reloaded = load_config()
    assert reloaded["model"] == "roundtrip"


def test_save_config_uses_existing_path(clean_env):
    path = clean_env / "custom.json"
    make_config_file(path, {"model": "m"})
    cfg = load_config(config_path=str(path))
    written = save_config(cfg)
    assert written == path
    assert json.loads(path.read_text())["model"] == "m"
