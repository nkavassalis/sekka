# Sekka

A lightweight terminal (TUI) chat client for any **OpenAI-compatible** LLM
endpoint (vLLM, llama.cpp server, Ollama's OpenAI API, LM Studio, ...).
Built for lightweight model exploration, simple text knowledge-base chat
bots, and role playing games.

```
┌──────────────────────────────────────────────┐
│ You:                                         │  chat history
│ hello!                                       │  (scrollable with
│                                              │   PageUp/PageDown,
│ Assistant:                                   │   split is configurable)
│ hi, how can I help?                          │
│ [1.4s, 38.2 tok/s]                           │
├──────────────────────────────────────────────┤
│ > type a message...                          │  input
│   (multi-line)                               │   (arrow keys move cursor)
└──────────────────────────────────────────────┘
```

## Features

- Generic OpenAI-compatible endpoints (`/models` + `/chat/completions`)
- Model discovery: if no model is given, sekka lists the endpoint's models
  and lets you pick one (or auto-selects when there is exactly one)
- Everything configurable via CLI flags, environment, or a JSON config file
- `/config` — in-app configuration screen (endpoint, model, system prompt, ...)
- `/save` — save the chat history to a timestamped file (asks first)
- Optional autosave after every reply (off by default)
- Configurable message colors and key bindings
- Dark bracketed timing line after each reply: `[1.4s, 38.2 tok/s]`
- Context meter in the corner of the editor (`45k/262k`) with configurable
  behaviour when the window fills: pause, roll old messages out, or compact
  them into a summary

## Install

```bash
pip install -e .          # from a clone of this repo
```

Requires Python >= 3.10. Dependencies: `textual`, `requests`.

## Quick start

```bash
sekka --endpoint http://10.1.13.99:8000/v1
# or pick a model directly:
sekka --endpoint http://10.1.13.99:8000/v1 --model qwen3.8-flash-next
```

Then just type and press **enter**. `/help` lists the in-chat commands.

## Usage

```
sekka [options]

--endpoint URL       OpenAI-compatible base URL, e.g. http://host:8000/v1
--model  MODEL       model id (omit to pick from the endpoint's /models)
--system  TEXT       system prompt
--api-key KEY        bearer token (only needed if the endpoint requires one)
--temperature FLOAT  sampling temperature
--max-tokens N       max tokens to generate
--timeout SECONDS    response timeout, 0 = wait forever (default 300)
--config PATH        use a specific config file
--version            show version
```

Environment variables (overridden by CLI flags):
`SEKKA_ENDPOINT`, `SEKKA_MODEL`, `SEKKA_API_KEY`, `SEKKA_CONFIG`.

Configuration precedence: **CLI flags > environment > config file > defaults**.

## Slash commands

| Command   | What it does                                            |
|-----------|---------------------------------------------------------|
| `/help`   | show commands and current key bindings                  |
| `/save`   | save history to `sekka_YYYYMMDD_HHMMSS.json` (asks first)|
| `/config` | open the configuration screen (saved to the config file)|
| `/models` | re-fetch models from the endpoint and pick one          |
| `/clear`  | clear the on-screen and sent chat history               |
| `/exit`   | quit (also **ctrl+q**)                                  |

## Keys

Defaults (configurable via the `keys` block in the config file, see
[docs/configuration.md](docs/configuration.md)):

| Key         | Action                          |
|-------------|---------------------------------|
| `enter`     | send message                    |
| `alt+enter` | insert newline in the input     |
| `pageup`    | scroll chat history up          |
| `pagedown`  | scroll chat history down        |
| `up/down`   | move cursor inside the input    |
| `ctrl+q`    | quit                            |

Note: this version of Textual (the TUI library) rebinds `ctrl+c` to "copy"
while an editor is focused; `ctrl+q` quits the app.

## Files

- Config: `./.sekka/config.json`, then `~/.sekka/config.json` (first one found
  wins; `/config` writes back to it). See
  [docs/configuration.md](docs/configuration.md).
- Saved chats: `sekka_YYYYMMDD_HHMMSS.json` (or `.md`) in `save_dir`.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

Layout: `sekka/config.py` (layered config), `sekka/client.py` (HTTP),
`sekka/commands.py` (slash parsing), `sekka/storage.py` (saving),
`sekka/stats.py` (timing line), `sekka/tui.py` (Textual app), `sekka/cli.py`.

## Credits

*sekka* (雪華) — “snowflake”: each chat crystallises one exchange at a time.

Built by [Qwen 3.8 Flash Next](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4). ⚡🇨🇦
