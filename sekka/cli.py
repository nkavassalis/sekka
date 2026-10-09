"""Command line entry point for sekka."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__
from .config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sekka",
        description="A terminal chat client for OpenAI-compatible LLM endpoints.",
    )
    parser.add_argument("--version", action="version", version=f"sekka {__version__}")
    parser.add_argument("--endpoint", help="OpenAI-compatible base URL, e.g. http://host:8000/v1")
    parser.add_argument("--model", help="model id (omit to pick from the endpoint's /models)")
    parser.add_argument("--system", dest="system_prompt", help="system prompt")
    parser.add_argument("--api-key", help="bearer token for the endpoint (optional)")
    parser.add_argument("--temperature", type=float, help="sampling temperature")
    parser.add_argument("--max-tokens", type=int, help="max tokens to generate")
    parser.add_argument(
        "--timeout",
        type=float,
        help="seconds to wait for a response (0 = wait forever); default 300",
    )
    parser.add_argument(
        "--reasoning",
        choices=["none", "minimal", "low", "medium", "high"],
        help="reasoning effort sent to the endpoint (none = don't send)",
    )
    parser.add_argument(
        "--history-percent",
        type=int,
        choices=range(50, 96),
        metavar="50-95",
        help="percent of the screen used by the chat history",
    )
    parser.add_argument(
        "--context-window",
        type=int,
        help="total context tokens for the meter (default: from the endpoint)",
    )
    parser.add_argument(
        "--context-mode",
        choices=["pause", "rolling", "compact"],
        help="what happens when the context window fills",
    )
    parser.add_argument(
        "--stream",
        action=argparse.BooleanOptionalAction,
        help="show replies token by token (--stream / --no-stream)",
    )
    parser.add_argument("--top-p", type=float, help="nucleus sampling cutoff (endpoint default if unset)")
    parser.add_argument("--min-p", type=float, help="min-p cutoff (vLLM/llama.cpp extension)")
    parser.add_argument("--presence-penalty", type=float, help="presence penalty")
    parser.add_argument("--frequency-penalty", type=float, help="frequency penalty")
    parser.add_argument(
        "--repetition-penalty", type=float, help="repetition penalty (vLLM/llama.cpp extension)"
    )
    parser.add_argument(
        "--stop",
        action="append",
        metavar="SEQ",
        help="stop sequence; repeat the flag for several (e.g. --stop 'Player: --')",
    )
    parser.add_argument(
        "--remember",
        action=argparse.BooleanOptionalAction,
        help="write --endpoint/--model into ./.sekka/config.json so the next bare"
             " sekka works (--remember / --no-remember)",
    )
    parser.add_argument(
        "--readonly",
        action=argparse.BooleanOptionalAction,
        help="play without touching settings: no /config edits, /models, /knowledge"
             " or /campaign, and nothing is written to disk"
             " (--readonly / --no-readonly)",
    )
    parser.add_argument(
        "--serve-readonly",
        action="store_true",
        help="shorthand for `serve -- --readonly`: every browser tab is read-only",
    )
    parser.add_argument("--save-dir", help="where /save and autosave write files")
    parser.add_argument(
        "--save-format", choices=["json", "markdown"], help="saved file format"
    )
    parser.add_argument(
        "--autosave",
        action=argparse.BooleanOptionalAction,
        help="auto-save history after every reply (--autosave / --no-autosave)",
    )
    parser.add_argument(
        "-r",
        "--resume",
        nargs="?",
        const="",
        metavar="FILE",
        help="resume a saved session; without FILE, pick one from the save directory",
    )
    parser.add_argument(
        "--campaign",
        help="campaign file (system prompt, cast, lore, greeting) to play as",
    )
    parser.add_argument(
        "--pack",
        metavar="NAME|DIR",
        help="play an installed pack by name, or a pack directory without installing it"
             " (beats the config's campaign; conflicts with --campaign)",
    )
    parser.add_argument(
        "run_mode",
        nargs="?",
        default="chat",
        choices=["chat", "serve", "pack"],
        metavar="chat|serve|pack",
        help="'chat' (default) runs in this terminal; 'serve' shows the same UI in"
             " a browser at http://127.0.0.1:8484 (needs the 'serve' extra);"
             " 'pack' manages installed campaign packs",
    )
    parser.add_argument(
        "pack_args",
        nargs="*",
        default=[],
        metavar="pack <command> ...",
        help="with 'pack': list | show NAME | install SRC | remove NAME | fork NAME [DIR]",
    )
    parser.add_argument("--name", dest="pack_name", help="with 'pack install': pack name to use")
    parser.add_argument(
        "--force",
        action="store_true",
        help="with 'pack install' replace an installed pack of the same name;"
             " with 'pack remove' discard a pack that still holds play state;"
             " with 'pack fork' overwrite the destination",
    )
    parser.add_argument("--serve-host", dest="serve_host", help="address for 'sekka serve' (default 127.0.0.1)")
    parser.add_argument("--serve-port", dest="serve_port", type=int, help="port for 'sekka serve' (default 8484)")
    parser.add_argument("--serve-title", help="browser tab title for 'sekka serve'")
    parser.add_argument(
        "--serve-public-url",
        dest="serve_public_url",
        help="absolute URL the page is reached at (e.g. https://play.example.net/sekka);"
             " use behind a reverse proxy or an SSH tunnel on another port, where the"
             " bind address is not what the browser typed",
    )
    parser.add_argument(
        "--serve-allow-public",
        action="store_true",
        help="let 'sekka serve' bind a non-loopback address (no auth: read the docs first)",
    )
    parser.add_argument(
        "--config",
        help="path to a config file (default: ./.sekka/config.json then ~/.sekka/config.json)",
    )
    return parser


# Flags that only mean something in `serve` mode. On a plain `sekka` run they are
# silently meaningless (run_mode defaults to 'chat'), which reads as "my web server
# started" when it did not - and worse, --serve-host/--serve-port would still be
# written into the config overrides. So: reject them before anything is loaded.
SERVE_ONLY_FLAGS = (
    "serve_host",
    "serve_port",
    "serve_title",
    "serve_public_url",
    "serve_allow_public",
    "serve_readonly",
)


def serve_only_flags(args: argparse.Namespace) -> list[str]:
    """Which --serve-* flags the user actually typed, as `--dashed-names`."""
    given = []
    for name in SERVE_ONLY_FLAGS:
        value = getattr(args, name, None)
        if value is True or (not isinstance(value, bool) and value is not None):
            given.append("--" + name.replace("_", "-"))
    return given


def serve_mode_error(args: argparse.Namespace) -> Optional[str]:
    """Refusal text when --serve-* flags appear without the `serve` mode."""
    if args.run_mode == "serve":
        return None
    stray = serve_only_flags(args)
    if not stray:
        return None
    return (
        f"{' '.join(stray)} only {'apply' if len(stray) > 1 else 'applies'} to 'sekka "
        f"serve'. Add the 'serve' mode to the command line, e.g. "
        f"'sekka serve {stray[0]} ...'. Nothing was served and nothing was written."
    )


PACK_USAGE = """usage: sekka pack <command>
  sekka pack list                                  installed packs
  sekka pack show NAME                             what a pack holds and where it lives
  sekka pack install SRC [--name NAME] [--force]   from a directory, .tar.gz, .tar or .zip
  sekka pack remove NAME [--force]                 delete an installed pack
  sekka pack fork NAME [DIR] [--force]             copy one out to a directory you own

SRC is always a path: sekka never fetches a pack over the network.
Play one with `sekka --pack NAME` (or SEKKA_PACK=NAME); packs live in ~/.sekka/packs
unless SEKKA_PACKS_DIR says otherwise."""

PACK_VERBS = ("list", "show", "install", "remove", "fork")
PACK_ARITY = {"list": (0, 0), "show": (1, 1), "install": (1, 1), "remove": (1, 1), "fork": (1, 2)}


def pack_usage_error(args: argparse.Namespace) -> Optional[str]:
    """Refusal text for a malformed `sekka pack ...` line, or None when it is fine.

    The verb grammar is checked here rather than by argparse so a mistake reads as a
    sentence about packs instead of a usage dump about the whole CLI.
    """
    if args.run_mode != "pack":
        return None
    words = list(args.pack_args or [])
    if not words:
        return "Which pack command?\n" + PACK_USAGE
    verb = words[0]
    if verb not in PACK_VERBS:
        return f"Unknown pack command '{verb}'.\n" + PACK_USAGE
    low, high = PACK_ARITY[verb]
    given = len(words) - 1
    if not low <= given <= high:
        wanted = "no arguments" if high == 0 else ("a name" if low == high == 1 else "a name and an optional destination")
        return f"'sekka pack {verb}' takes {wanted}, got {given}."
    if verb != "install" and args.pack_name:
        return f"--name only applies to 'sekka pack install'.\n" + PACK_USAGE
    if verb == "fork" and given == 1 and args.force:
        return None
    return None


def run_pack_command(args: argparse.Namespace) -> int:
    """Handle `sekka pack ...`. Never starts a session, never talks to an endpoint."""
    from . import packs

    words = list(args.pack_args or [])
    verb, rest = words[0], words[1:]
    try:
        if verb == "list":
            return _pack_list()
        if verb == "show":
            return _pack_show(rest[0])
        if verb == "install":
            return _pack_install(rest[0], args.pack_name, args.force)
        if verb == "remove":
            gone = packs.remove(rest[0], force=args.force)
            print(f"Removed pack {gone}")
            return 0
        if verb == "fork":
            name = rest[0]
            target = packs.fork_pack(name, rest[1] if len(rest) > 1 else f"{packs.slugify(name)}-custom", force=args.force)
            print(f"Copied pack '{name}' to {target}")
            print(f"  edit it and play it from there: sekka --pack {target}")
            print("  a fork is yours: /note writes into it, and reinstalling the original will not touch it")
            return 0
    except packs.PackError as exc:
        print(f"sekka: {exc}", file=sys.stderr)
        return 2
    return 2


def _pack_list() -> int:
    from . import packs

    rows = packs.list_packs()
    if not rows:
        print(f"No packs installed. Packs live in: {packs.packs_dir()}")
        print("Install one with: sekka pack install <directory or archive>")
        return 0
    for row in rows:
        lore = f", {len(row['knowledge'])} lore files" if row["knowledge"] else ""
        broken = f"  [{row['error']}]" if row["error"] else ""
        print(f"  {row['name']:<26} {row['title']}{lore}{broken}")
    return 0


def _pack_show(spec: str) -> int:
    from . import packs

    found = packs.resolve_pack_dir(spec)
    data = packs.check_pack(packs.campaign_file_for(found))
    labels = data.get("labels") or {}
    note = str(data.get("note") or "").strip()
    print(data.get("name") or spec)
    print(f"  pack directory   {found}")
    print(f"  lore resolves    {packs.pack_base_dir(found)}")
    print(f"  labels           {labels.get('user', 'You')} / {labels.get('assistant', 'Assistant')}")
    print(f"  note             {('holds play state: ' + note[:56]) if note else 'empty (no play state)'}")
    for entry in data.get("knowledge", []) or []:
        state = "on " if entry.get("enabled", True) else "off"
        print(f"  lore  [{state}] {entry.get('file')}")
        print(f"         {str(entry.get('description', '')).strip()[:96]}")
    for warning in data.get("_pack_warnings", []):
        print(f"  warning: disabled lore file not found: {warning}")
    return 0


def _pack_install(source: str, name: Optional[str], force: bool) -> int:
    from . import packs

    installed = packs.install(source, name=name, force=force)
    print(f"Installed pack '{installed['name']}' - {installed['title']}")
    print(f"  at {installed['path']}")
    print(f"  play it: sekka --pack {installed['name']}")
    for warning in installed["warnings"]:
        print(f"  warning: disabled lore file not found: {warning}")
    return 0


def split_passthrough(argv: Optional[list[str]]) -> tuple[list[str], list[str]]:
    """Split argv on a bare `--`.

    Everything before it is parsed normally; everything after it is forwarded
    verbatim to each `sekka` process the web server spawns, so
    `sekka serve -- --endpoint http://box:8000/v1` serves that endpoint.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        return argv, []
    cut = argv.index("--")
    return argv[:cut], argv[cut + 1:]


def main(argv: Optional[list[str]] = None) -> int:
    own_args, passthrough = split_passthrough(argv)
    args = build_parser().parse_args(own_args)
    refusal = serve_mode_error(args)
    if refusal:
        print(f"sekka: {refusal}", file=sys.stderr)
        return 2
    refusal = pack_usage_error(args)
    if refusal:
        print(f"sekka: {refusal}", file=sys.stderr)
        return 2
    if args.run_mode == "pack":
        return run_pack_command(args)
    if args.pack_args:
        print(
            "sekka: " + " ".join(args.pack_args) + " only means something after 'sekka pack'. "
            "To play a pack, say so: sekka --pack NAME",
            file=sys.stderr,
        )
        return 2
    if args.pack_name or args.force:
        print("sekka: --name/--force belong to 'sekka pack install|remove|fork'.", file=sys.stderr)
        return 2
    overrides = {
        "endpoint": args.endpoint,
        "model": args.model,
        "system_prompt": args.system_prompt,
        "api_key": args.api_key,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "top_p": args.top_p,
        "min_p": args.min_p,
        "presence_penalty": args.presence_penalty,
        "frequency_penalty": args.frequency_penalty,
        "repetition_penalty": args.repetition_penalty,
        "stop": args.stop,
        "request_timeout": args.timeout,
        "reasoning": args.reasoning,
        "stream": args.stream,
        "history_percent": args.history_percent,
        "context_window": args.context_window,
        "context_mode": args.context_mode,
        "save_dir": args.save_dir,
        "save_format": args.save_format,
        "autosave": args.autosave,
        "remember": args.remember,
        "readonly": args.readonly,
        "serve_host": args.serve_host,
        "serve_port": args.serve_port,
    }
    try:
        config = load_config(
            overrides, config_path=args.config, campaign_path=args.campaign, pack=args.pack
        )
    except ConfigError as exc:
        print(f"sekka: {exc}", file=sys.stderr)
        return 2

    if args.run_mode == "serve":
        from .serve import run_server

        # The scenario has to travel to each tab: textual-serve spawns a fresh
        # `python -m sekka` per websocket, which re-parses its own command line and
        # would otherwise come up with no campaign at all. Without this, `sekka serve
        # --pack frostspire` serves a blank generic chat while the flag says otherwise.
        scenario = []
        if args.pack:
            scenario += ["--pack", args.pack]
        if args.campaign:
            scenario += ["--campaign", args.campaign]
        if args.config:
            scenario += ["--config", args.config]
        served_flags = scenario + passthrough + (["--readonly"] if args.serve_readonly else [])
        return run_server(
            config,
            host=args.serve_host,
            port=args.serve_port,
            title=args.serve_title,
            extra_args=served_flags,
            allow_public=args.serve_allow_public,
            public_url=args.serve_public_url,
        )

    from .tui import SekkaApp

    app = SekkaApp(config, resume=args.resume)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
