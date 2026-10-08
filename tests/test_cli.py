import pytest

from sekka import cli


def captured_main(monkeypatch, argv):
    """Run cli.main with load_config/SekkaApp stubbed; return overrides + path."""
    box = {}

    def fake_load(overrides, config_path=None, campaign_path=None):
        box["overrides"] = overrides
        box["config_path"] = config_path
        box["campaign_path"] = campaign_path
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
        "--stream",
        "--top-p", "0.9",
        "--min-p", "0.05",
        "--presence-penalty", "0.1",
        "--frequency-penalty", "0.2",
        "--repetition-penalty", "1.1",
        "--stop", "Player:",
        "--stop", "GM:",
        "--save-dir", "/tmp/saves",
        "--save-format", "markdown",
        "--autosave",
        "--remember",
        "--serve-host", "0.0.0.0",
        "--serve-port", "9000",
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
        "stream": True,
        "save_dir": "/tmp/saves",
        "save_format": "markdown",
        "autosave": True,
        "top_p": 0.9,
        "min_p": 0.05,
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "repetition_penalty": 1.1,
        "stop": ["Player:", "GM:"],
        "remember": True,
        "serve_host": "0.0.0.0",
        "serve_port": 9000,
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
        "--top-p", "0.9", "--min-p", "0.05", "--presence-penalty", "0.1",
        "--frequency-penalty", "0.2", "--repetition-penalty", "1.1", "--stop", "Player:",
        "--stop", "GM:",
        "--reasoning", "none", "--history-percent", "60", "--context-window",
        "1024", "--context-mode", "pause", "--save-dir", ".", "--save-format",
        "json", "--autosave",
    ])
    # long prose/campaign-shaped keys live in files, /config and /note, not flags;
    # 'campaign' is a loader argument (it selects a file), not a value override
    structured = {
        "labels", "theme", "keys", "knowledge", "greeting", "player", "note", "campaign"
    }
    scalars = set(DEFAULT_CONFIG) - structured
    # the overrides dict must cover every scalar config key exactly
    assert set(box["overrides"]) == scalars
    assert box["campaign_path"] is None


def test_no_stream_flag_reaches_config(monkeypatch):
    box = captured_main(monkeypatch, ["--no-stream"])
    assert box["overrides"]["stream"] is False


def test_campaign_flag_is_passed_to_the_loader(monkeypatch):
    box = captured_main(monkeypatch, ["--campaign", "camp.json"])
    assert box["campaign_path"] == "camp.json"


def test_no_remember_flag_reaches_config(monkeypatch):
    box = captured_main(monkeypatch, ["--no-remember"])
    assert box["overrides"]["remember"] is False
