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
        "--config",
        help="path to a config file (default: ./.sekka/config.json then ~/.sekka/config.json)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {
        "endpoint": args.endpoint,
        "model": args.model,
        "system_prompt": args.system_prompt,
        "api_key": args.api_key,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "request_timeout": args.timeout,
        "reasoning": args.reasoning,
        "history_percent": args.history_percent,
        "context_window": args.context_window,
        "context_mode": args.context_mode,
        "save_dir": args.save_dir,
        "save_format": args.save_format,
        "autosave": args.autosave,
    }
    try:
        config = load_config(overrides, config_path=args.config)
    except ConfigError as exc:
        print(f"sekka: {exc}", file=sys.stderr)
        return 2

    from .tui import SekkaApp

    app = SekkaApp(config)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
