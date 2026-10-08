"""Serve the same terminal UI over a local web page.

Thin wrapper around `textual-serve`, which runs the app as a subprocess attached
to a pseudo-terminal and streams the real terminal rendering to the browser. The
web UI is therefore not a reimplementation: it is `sekka` itself, pixel for
pixel, in an xterm.js widget - commands, keys, themes, streaming, all of it.

Implications worth knowing before you expose it:

- Each browser tab gets its own `sekka` subprocess, so each has its own chat.
  They do share the config file, the campaign file and the save directory.
- There is no authentication. Loopback binding is the default for a reason.
"""

from __future__ import annotations

import shlex
import sys
from typing import Optional, Sequence

from .config import Config, ConfigError

INSTALL_HINT = (
    "The web server needs the optional 'serve' extra:\n"
    "\n"
    "    pip install 'sekka[serve]'\n"
    "    # or, if you installed with pipx:\n"
    "    pipx inject sekka textual-serve\n"
)

# 0.0.0.0 deliberately is not in here: it accepts connections from anywhere.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def serve_command(extra_args: Sequence[str] = ()) -> str:
    """The shell command textual-serve spawns for each browser tab.

    Runs this same interpreter/module so the served app matches the installed
    sekka, with any pass-through flags appended. Every token is quoted: the
    command is handed to a shell, and the flags come from the user's command
    line, so an argument like `--stop $(rm -rf /)` must not be interpreted.
    """
    argv = [sys.executable, "-m", "sekka", *extra_args]
    return " ".join(shlex.quote(part) for part in argv)


def resolve_bind(config: Config, host: Optional[str], port: Optional[int]) -> tuple[str, int]:
    """Host/port to bind, with the config file as the fallback."""
    bind_host = host or config.get("serve_host", "127.0.0.1")
    bind_port = port or config.get("serve_port", 8484)
    try:
        bind_port = int(bind_port)
    except (TypeError, ValueError):
        raise ConfigError("Config 'serve_port' must be an integer port number.") from None
    if not 1 <= bind_port <= 65535:
        raise ConfigError("Config 'serve_port' must be between 1 and 65535.")
    return str(bind_host), bind_port


def public_bind_warning(host: str) -> Optional[str]:
    """Return a warning when `host` is reachable from outside this machine."""
    if host in LOOPBACK_HOSTS:
        return None
    return (
        f"Binding to {host} - this server has NO authentication and no HTTPS. "
        "Anyone who can reach the port can chat through your endpoint, read the "
        "transcripts on screen and change the saved config. Prefer an SSH tunnel "
        "(ssh -L 8484:127.0.0.1:8484 host) or a reverse proxy with auth."
    )


def run_server(
    config: Config,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    title: Optional[str] = None,
    extra_args: Sequence[str] = (),
    allow_public: bool = False,
) -> int:
    """Start the web server (blocking). Returns a process exit code."""
    try:
        from textual_serve.server import Server
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        return 2

    bind_host, bind_port = resolve_bind(config, host, port)
    warning = public_bind_warning(bind_host)
    if warning:
        if not allow_public:
            print(
                f"sekka: {warning}\n"
                "Refusing to bind a non-loopback address. Re-run with --serve-allow-public "
                "if you really mean it (behind a VPN or an authenticated proxy).",
                file=sys.stderr,
            )
            return 2
        print(f"sekka: WARNING {warning}", file=sys.stderr)

    server = Server(
        serve_command(extra_args),
        host=bind_host,
        port=bind_port,
        title=title or "sekka",
    )
    print(f"sekka web UI: http://{bind_host}:{bind_port}  (ctrl+c to stop)")
    print(f"each browser tab starts a fresh chat; config comes from {config.path or 'defaults'}")
    server.serve()
    return 0
