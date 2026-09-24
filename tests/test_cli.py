import pytest

from sekka import cli


def captured_main(monkeypatch, argv):
    """Run cli.main with load_config/SekkaApp stubbed; return overrides + path."""
    box = {}

    def fake_load(overrides, config_path=None):
        box["overrides"] = overrides
        box["config_path"] = config_path
        return object()

    class FakeApp:
        def __init__(self, config, resume=None):
            box["config"] = config
            box["resume"] = resume

        def run(self):
            box["ran"] = True

    monkeypatch.setattr(cli, "load_config", fake_load)
    monkeypatch.setattr("sekka.tui.SekkaApp", FakeApp)
    assert cli.main(argv) == 0
    return box


def test_no_flags_yields_all_none_overrides(monkeypatch):
    box = captured_main(monkeypatch, [])
    # every override None -> load_config ignores them all
    assert all(v is None for v in box["overrides"].values())
    assert box["ran"]


def test_every_flag_maps_to_its_config_key(monkeypatch):
    box = captured_main(monkeypatch, [
        "--endpoint", "http://h:8000/v1",
        "--model", "m1",
        "--system", "be brief",
        "--api-key", "sk-1",
        "--temperature", "0.2",
        "--max-tokens", "512",
        "--timeout", "600",
        "--reasoning", "low",
        "--history-percent", "70",
        "--context-window", "16384",
        "--context-mode", "rolling",
        "--save-dir", "/tmp/saves",
        "--save-format", "markdown",
        "--autosave",
    ])
    assert box["overrides"] == {
        "endpoint": "http://h:8000/v1",
        "model": "m1",
        "system_prompt": "be brief",
        "api_key": "sk-1",
        "temperature": 0.2,
        "max_tokens": 512,
        "request_timeout": 600.0,
        "reasoning": "low",
        "history_percent": 70,
        "context_window": 16384,
        "context_mode": "rolling",
        "save_dir": "/tmp/saves",
        "save_format": "markdown",
        "autosave": True,
    }


def test_no_autosave_flag_is_false_not_none(monkeypatch):
    box = captured_main(monkeypatch, ["--no-autosave"])
    assert box["overrides"]["autosave"] is False


def test_config_path_passed_through(monkeypatch):
    box = captured_main(monkeypatch, ["--config", "/x/y.json"])
    assert box["config_path"] == "/x/y.json"


def test_resume_variations(monkeypatch):
    assert captured_main(monkeypatch, [])["resume"] is None       # no flag
    assert captured_main(monkeypatch, ["-r"])["resume"] == ""     # picker
    assert captured_main(monkeypatch, ["-r", "s.json"])["resume"] == "s.json"
    assert captured_main(monkeypatch, ["--resume", "s.json"])["resume"] == "s.json"


@pytest.mark.parametrize("args", [
    ["--context-mode", "turbo"],
    ["--save-format", "yaml"],
    ["--reasoning", "ultra"],
    ["--history-percent", "40"],
    ["--temperature", "hot"],
])
def test_bad_flag_values_exit_2(args):
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(args)
    assert excinfo.value.code == 2


def test_all_scalar_config_keys_have_a_flag(monkeypatch):
    """Every non-structured config key must be reachable from the CLI.

    Structured keys (labels/theme/keys/knowledge) are config-file and
    /config-screen only by design.
    """
    from sekka.config import DEFAULT_CONFIG

    box = captured_main(monkeypatch, [
        "--endpoint", "e", "--model", "m", "--system", "s", "--api-key", "k",
        "--temperature", "0.5", "--max-tokens", "10", "--timeout", "10",
        "--reasoning", "none", "--history-percent", "60", "--context-window",
        "1024", "--context-mode", "pause", "--save-dir", ".", "--save-format",
        "json", "--autosave",
    ])
    structured = {"labels", "theme", "keys", "knowledge"}
    scalars = set(DEFAULT_CONFIG) - structured
    # the overrides dict must cover every scalar config key exactly
    assert set(box["overrides"]) == scalars
