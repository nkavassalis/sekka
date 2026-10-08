"""Wiring for `sekka serve` (the aiohttp server itself is exercised separately)."""

import json

import pytest

from sekka.config import DEFAULT_CONFIG, ConfigError, validate_config
from sekka.serve import (
    LOOPBACK_HOSTS,
    public_bind_warning,
    resolve_bind,
    serve_command,
)


class FakeConfig(dict):
    path = None

    def get(self, key, default=None):
        return super().get(key, DEFAULT_CONFIG.get(key, default))


def test_served_command_runs_this_module():
    import shlex

    command = serve_command(["--endpoint", "http://box:8000/v1"])
    tokens = shlex.split(command)
    assert tokens[1:3] == ["-m", "sekka"]
    assert tokens[3:] == ["--endpoint", "http://box:8000/v1"]


def test_dangerous_passthrough_stays_inert_when_the_shell_splits_it():
    """The command is handed to a shell, so tokens must survive splitting as data."""
    import shlex
    import subprocess

    nasty = ["--stop", "a; touch /tmp/sekka-should-not-exist", "--system", "$(whoami)"]
    command = serve_command(nasty)
    assert shlex.split(command)[3:] == nasty          # round-trips as data, not syntax

    # prove it by running the same shape through a shell that only echoes
    probe = " ".join(shlex.quote(part) for part in ["echo", *nasty])
    out = subprocess.run(["sh", "-c", probe], capture_output=True, text=True).stdout
    assert "sekka-should-not-exist" in out and "$(whoami)" in out
    import pathlib
    assert not pathlib.Path("/tmp/sekka-should-not-exist").exists()


def test_bind_defaults_to_loopback_and_8484():
    host, port = resolve_bind(FakeConfig(), None, None)
    assert (host, port) == ("127.0.0.1", 8484)


def test_flags_override_the_config_file():
    cfg = FakeConfig({"serve_host": "127.0.0.5", "serve_port": 7000})
    assert resolve_bind(cfg, None, None) == ("127.0.0.5", 7000)
    assert resolve_bind(cfg, "127.0.0.9", 9191) == ("127.0.0.9", 9191)


def test_bad_port_is_a_config_error():
    for bad in (0, 70000, "http"):
        cfg = FakeConfig({"serve_port": bad})
        with pytest.raises(ConfigError):
            resolve_bind(cfg, None, None)


def test_loopback_needs_no_warning_and_public_needs_one():
    for host in ("127.0.0.1", "localhost", "::1"):
        assert public_bind_warning(host) is None
    # TEST-NET addresses (RFC 5737): documentation space, routable nowhere, so the
    # fixture cannot accidentally describe a real host on anyone's network.
    for host in ("0.0.0.0", "198.51.100.7", "192.0.2.1"):
        warning = public_bind_warning(host)
        assert warning and "NO authentication" in warning
    assert "0.0.0.0" not in LOOPBACK_HOSTS


def test_serve_config_validation():
    def base(**over):
        values = json.loads(json.dumps(DEFAULT_CONFIG))
        values.update(over)
        return values

    validate_config(base(serve_host="0.0.0.0", serve_port=8484))
    for bad in ({"serve_host": ""}, {"serve_host": None}, {"serve_port": "8484"},
                {"serve_port": 0}, {"serve_port": True}):
        with pytest.raises(ConfigError):
            validate_config(base(**bad))


def test_missing_extra_explains_how_to_install_it(monkeypatch, capsys):
    """No silent failure: the hint has to name the extra."""
    import builtins

    from sekka.serve import run_server

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name.startswith("textual_serve"):
            raise ImportError("no such package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    code = run_server(FakeConfig(), host="127.0.0.1", port=8484)
    err = capsys.readouterr().err
    assert code == 2
    assert "sekka[serve]" in err and "pipx inject" in err


def test_public_bind_is_refused_without_explicit_opt_in(capsys):
    from sekka.serve import run_server

    assert run_server(FakeConfig(), host="0.0.0.0", port=8484) == 2
    err = capsys.readouterr().err
    assert "Refusing to bind a non-loopback address" in err
    assert "--serve-allow-public" in err


def test_cli_serve_subcommand_is_parsed():
    from sekka.cli import build_parser, split_passthrough

    own, extra = split_passthrough(
        ["serve", "--serve-host", "127.0.0.9", "--serve-port", "9000",
         "--", "--endpoint", "http://h/v1"]
    )
    args = build_parser().parse_args(own)
    assert args.run_mode == "serve"
    assert args.serve_host == "127.0.0.9" and args.serve_port == 9000
    assert extra == ["--endpoint", "http://h/v1"]


# --- the page sekka serves (sekka/templates/app_index.html) --------------------

def template_text():
    from sekka.serve import TEMPLATES_PATH

    return (TEMPLATES_PATH / "app_index.html").read_text()


def test_shipped_template_is_found_by_a_relative_import():
    """run_server passes TEMPLATES_PATH; aiohttp needs the dir to exist and hold it."""
    from sekka.serve import TEMPLATES_PATH

    assert TEMPLATES_PATH.is_dir() and (TEMPLATES_PATH / "app_index.html").is_file()


def test_our_page_replaces_the_two_upstream_breakages():
    html = template_text()
    assert 'href="http' not in html and 'src="http' not in html   # nothing third-party loads
    assert 'href="static/css/xterm.css"' in html          # relative, survives a proxy prefix
    assert "location.host" in html                       # websocket is same-origin
    assert "wss" in html                                 # https stays wss


def captured_server_kwargs(monkeypatch, **run_kwargs):
    """Run run_server with a fake Server, returning what it was handed."""
    pytest.importorskip("textual_serve")
    import sys
    import types

    from sekka.serve import run_server

    from sekka.serve import run_server

    seen = {}

    class FakeServer:
        def __init__(self, command, **kwargs):
            seen["command"] = command
            seen.update(kwargs)

        def serve(self):
            seen["served"] = True

    module = types.ModuleType("textual_serve.server")
    module.Server = FakeServer
    monkeypatch.setitem(sys.modules, "textual_serve.server", module)
    monkeypatch.setitem(sys.modules, "textual_serve", types.ModuleType("textual_serve"))
    code = run_server(FakeConfig(), host="127.0.0.1", port=8484, **run_kwargs)
    assert code == 0 and seen.get("served")
    return seen


def test_default_page_comes_from_sekka_not_textual_serve(monkeypatch):
    seen = captured_server_kwargs(monkeypatch)
    from sekka.serve import TEMPLATES_PATH

    assert seen["templates_path"] == TEMPLATES_PATH
    assert "public_url" not in seen


def test_public_url_opts_out_of_the_local_template(monkeypatch):
    seen = captured_server_kwargs(monkeypatch, public_url="https://play.example.net/sekka/")
    assert seen["public_url"] == "https://play.example.net/sekka"
    assert "templates_path" not in seen                   # stock template, absolute URLs


def test_public_url_must_be_absolute(capsys):
    from sekka.serve import run_server

    for bad in ("play.example.net", "//play.example.net", "ftp://x", ""):
        assert run_server(FakeConfig(), host="127.0.0.1", port=8484, public_url=bad) == 2
        assert "http://" in capsys.readouterr().err


def test_public_url_is_a_serve_only_flag():
    """It must not be droppable on a plain chat run, like the other --serve-* flags."""
    from sekka.cli import SERVE_ONLY_FLAGS, build_parser, serve_mode_error

    assert "serve_public_url" in SERVE_ONLY_FLAGS
    args = build_parser().parse_args(["--serve-public-url", "https://h/sekka"])
    assert "sekka serve" in serve_mode_error(args)
