import pytest

from sekka import cli


def captured_main(monkeypatch, argv):
    """Run cli.main with load_config/SekkaApp/run_server stubbed; return overrides + path."""
    box = {}

    def fake_load(overrides, config_path=None, campaign_path=None, pack=None):
        box["overrides"] = overrides
        box["config_path"] = config_path
        box["campaign_path"] = campaign_path
        box["pack"] = pack
        return object()

    class FakeApp:
        def __init__(self, config, resume=None):
            box["config"] = config
            box["resume"] = resume

        def run(self):
            box["ran"] = True

    def fake_run_server(config, *, host=None, port=None, title=None,
                        extra_args=(), allow_public=False, public_url=None):
        box["served"] = {
            "host": host,
            "port": port,
            "title": title,
            "extra_args": list(extra_args),
            "allow_public": allow_public,
            "public_url": public_url,
        }
        return 0

    monkeypatch.setattr(cli, "load_config", fake_load)
    monkeypatch.setattr("sekka.tui.SekkaApp", FakeApp)
    monkeypatch.setattr("sekka.serve.run_server", fake_run_server)
    assert cli.main(argv) == 0
    return box


def test_no_flags_yields_all_none_overrides(monkeypatch):
    box = captured_main(monkeypatch, [])
    # every override None -> load_config ignores them all
    assert all(v is None for v in box["overrides"].values())
    assert box["ran"]


def test_every_flag_maps_to_its_config_key(monkeypatch):
    # served run: --serve-* flags are only accepted alongside the 'serve' mode
    box = captured_main(monkeypatch, [
        "serve",
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
        "--readonly",
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
        "readonly": True,
        "serve_host": "0.0.0.0",
        "serve_port": 9000,
    }
    assert box["served"] == {
        "host": "0.0.0.0", "port": 9000, "title": None,
        "extra_args": [], "allow_public": False, "public_url": None,
    }


SERVE_ONLY = [
    ["--serve-host", "0.0.0.0"],
    ["--serve-port", "9100"],
    ["--serve-title", "table"],
    ["--serve-allow-public"],
    ["--serve-readonly"],
    ["--serve-public-url", "https://play.example.net/sekka"],
    # 0 is falsy but not absent: still a flag the user typed
    ["--serve-port", "0"],
]


@pytest.mark.parametrize("argv", SERVE_ONLY)
def test_serve_flags_without_the_serve_mode_are_rejected(monkeypatch, capsys, argv):
    """--serve-* on a plain `sekka` must not quietly open the TUI instead."""
    def boom(*a, **kw):
        raise AssertionError("load_config must not run: nothing may be written")

    monkeypatch.setattr(cli, "load_config", boom)
    assert cli.main(argv + ["--remember"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("sekka: --serve-")
    assert "sekka serve" in err


def test_serve_mode_error_names_every_stray_flag():
    args = cli.build_parser().parse_args(["--serve-port", "9100", "--serve-readonly"])
    msg = cli.serve_mode_error(args)
    assert "--serve-port" in msg and "--serve-readonly" in msg


@pytest.mark.parametrize("argv", [list(f) for f in SERVE_ONLY] + [["--serve-allow-public"]])
def test_serve_flags_are_accepted_when_the_mode_is_there(monkeypatch, argv):
    # argparse takes the positional anywhere, so `--serve-port N serve` is a serve run
    box = captured_main(monkeypatch, argv + ["serve"])
    assert "served" in box and "ran" not in box


def test_plain_chat_run_is_untouched_by_the_guard(monkeypatch):
    box = captured_main(monkeypatch, ["--readonly", "--endpoint", "http://h:8000/v1"])
    assert box["ran"] and "served" not in box


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


def test_serve_public_url_reaches_the_server(monkeypatch):
    box = captured_main(monkeypatch, ["serve", "--serve-public-url",
                                      "https://play.example.net/sekka"])
    assert box["served"]["public_url"] == "https://play.example.net/sekka"


# ----------------------------------------------------------- pack verb surface


def fake_pack_box(monkeypatch, **overrides):
    """Stub sekka.packs so cli pack commands can be tested without touching disk."""
    box = {"calls": [], **overrides}

    def record(name, result):
        def run(*args, **kwargs):
            box["calls"].append((name, args, kwargs))
            if isinstance(result, Exception):
                raise result
            return result

        return run

    import sekka.packs as packs

    for name, result in box.pop("stubs", {}).items():
        monkeypatch.setattr(packs, name, record(name, result))
    monkeypatch.setattr(packs, "packs_dir", lambda override=None: __import__("pathlib").Path("/tmp/packs"))
    return box


def test_pack_list_with_nothing_installed(monkeypatch, capsys):
    monkeypatch.setattr("sekka.packs.list_packs", lambda root=None: [])
    assert cli.main(["pack", "list"]) == 0
    out = capsys.readouterr().out
    assert "No packs installed" in out and "install" in out


def test_pack_list_prints_one_line_per_pack(monkeypatch, capsys):
    monkeypatch.setattr(
        "sekka.packs.list_packs",
        lambda root=None: [
            {
                "name": "frostspire",
                "title": "The Frostspire Marches",
                "knowledge": [{"file": "knowledge/world.md", "enabled": True}],
                "error": "",
            }
        ],
    )
    assert cli.main(["pack", "list"]) == 0
    out = capsys.readouterr().out
    assert "frostspire" in out and "The Frostspire Marches" in out and "1 lore files" in out


def test_pack_install_reports_the_name_to_type(monkeypatch, capsys):
    monkeypatch.setattr(
        "sekka.packs",
        type("P", (), {
            "install": staticmethod(lambda source, name=None, force=False: {
                "name": "frostspire", "title": "The Frostspire Marches",
                "path": __import__("pathlib").Path("/tmp/packs/frostspire"), "warnings": [],
            }),
            "PackError": __import__("sekka.packs", fromlist=["PackError"]).PackError,
        }),
        raising=False,
    )
    assert cli.main(["pack", "install", "examples/frostspire"]) == 0
    out = capsys.readouterr().out
    assert "--pack frostspire" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["pack"],
        ["pack", "wat"],
        ["pack", "install"],
        ["pack", "install", "a", "b"],
        ["pack", "list", "extra"],
        ["pack", "remove"],
        ["pack", "show"],
    ],
)
def test_bad_pack_commands_exit_2_with_pack_usage(argv, capsys):
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "sekka pack" in err               # pack usage, not the global argparse dump
    assert "--serve-port" not in err          # and no nonsense about serve flags


def test_pack_verbs_reject_serve_flags(capsys):
    """A pack command never starts a session, so serve flags there are a typo."""
    for argv in (["pack", "install", "x", "--serve-port", "9"], ["--serve-port", "9", "pack", "list"]):
        assert cli.main(argv) == 2
        err = capsys.readouterr().err
        assert "--serve-port" in err and "sekka serve" in err


def test_pack_name_and_force_only_mean_something_with_pack(capsys):
    assert cli.main(["--name", "x", "--force", "chat"]) == 2
    assert "pack" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["install", "examples/frostspire"], ["packs"], ["remove", "x"]])
def test_a_bare_pack_verb_is_still_rejected(argv, capsys):
    """`sekka install x` must not quietly work: the verb is `sekka pack install x`."""
    with pytest.raises(SystemExit) as stop:      # argparse refuses the mode word
        cli.main(argv)
    assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err.lower()


def test_pack_flag_is_passed_to_the_loader(monkeypatch):
    box = {}

    def fake_load(overrides, config_path=None, campaign_path=None, pack=None):
        box["pack"] = pack
        box["campaign"] = campaign_path
        return object()

    monkeypatch.setattr(cli, "load_config", fake_load)
    monkeypatch.setattr("sekka.tui.SekkaApp", lambda config, resume=None: type("A", (), {"run": lambda self: None})())
    assert cli.main(["--pack", "frostspire"]) == 0
    assert box["pack"] == "frostspire" and box["campaign"] is None


def test_pack_and_campaign_together_are_refused(monkeypatch, capsys):
    """Two scenarios in play at once is never what anyone wanted; the loader says so."""
    from sekka.config import ConfigError

    def refuse(*args, **kwargs):
        raise ConfigError("A pack and a campaign file both pick the scenario, and only one can win.")

    monkeypatch.setattr(cli, "load_config", refuse)
    assert cli.main(["--pack", "frostspire", "--campaign", "c.json"]) == 2
    assert "only one can win" in capsys.readouterr().err


def test_serve_forwards_the_scenario_to_each_tab(monkeypatch):
    box = {}

    def fake_run_server(config, *, host=None, port=None, title=None, extra_args=(),
                        allow_public=False, public_url=None):
        box["extra_args"] = list(extra_args)
        return 0

    monkeypatch.setattr(cli, "load_config", lambda *a, **k: object())
    monkeypatch.setattr("sekka.serve.run_server", fake_run_server)
    cli.main(["serve", "--pack", "frostspire"])
    args = box["extra_args"]
    assert args[:2] == ["--pack", "frostspire"]      # each tab re-parses this
