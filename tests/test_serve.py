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
    for host in ("0.0.0.0", "10.1.13.99", "0.0.0.0"):
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
