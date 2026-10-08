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
- **Streaming**: replies appear token by token (`--stream` / `--no-stream`),
  with `ctrl+x` to stop a reply and keep what arrived
- Model discovery: if no model is given, sekka lists the endpoint's models
  and lets you pick one (or auto-selects when there is exactly one)
- Everything configurable via CLI flags, environment, or a JSON config file
- `/config` — in-app configuration screen (endpoint, model, system prompt, ...)
- `/save` — save the chat history to a timestamped file (asks first)
- Optional autosave after every reply, updating one session file (off by default)
- Configurable message colors and key bindings
- History stops auto-scrolling when you scroll up to reread (it follows again
  once you return to the bottom)
- Dark bracketed timing line after each reply: `[1.4s, 38.2 tok/s]`
- Sessions: `/save` writes a resumable JSON file, and `sekka -r session.json`
  (or bare `sekka -r` for a picker) continues where you left off
- Knowledge files as tools: `/knowledge` turns process docs, character cards,
  or lore into read-only tools the model can consult (tool-calling models
  only; the model can never read files you didn't list)
- Take-backs for collaborative storytelling: `/undo`, `/edit`, `/regen` and
  `/swipe` (multiple generated versions of a reply, cycled without losing them)
- **Campaigns**: a `campaign.json` beside your config holds the system prompt,
  role labels, lore files, an opening scene and your character, so a scenario is
  one folder you `cd` into and play. Saved sessions remember their campaign and
  resume as themselves
- Optional thinking overlay (`/thinking`, `ctrl+t`) shows the model's
  reasoning and tool calls - hidden by default
- Context meter in the corner of the editor (`45k/262k`) with configurable
  behaviour when the window fills: pause, roll old messages out, or compact
  them into a summary

## Install

```bash
pipx install git+https://github.com/nkavassalis/sekka.git
```

(or `uv tool install git+https://github.com/nkavassalis/sekka.git`, or from a
clone: `pip install -e .`). Requires Python >= 3.10; dependencies: `textual`,
`requests`. Sekka is pure Python on purpose - the endpoint does the heavy
lifting, so bundled-binary builds (Nuitka/PyInstaller) would add ~50 MB for
zero speedup and are deliberately not shipped.

## Quick start

```bash
sekka --endpoint http://your-llm-server:8000/v1
# or pick a model directly:
sekka --endpoint http://your-llm-server:8000/v1 --model your-model-id
```

Then just type and press **enter**. `/help` lists the in-chat commands.

Pointing at a folder that contains a `campaign.json` (see
[`examples/roleplaying/`](examples/)) makes that the scenario you are playing:
`sekka --endpoint http://your-llm-server:8000/v1` inside it is enough.

## Usage

```
sekka [options]

--endpoint URL         OpenAI-compatible base URL, e.g. http://host:8000/v1
--model  MODEL         model id (omit to pick from the endpoint's /models)
--system  TEXT         system prompt
--api-key KEY          bearer token (only needed if the endpoint requires one)
--temperature FLOAT    sampling temperature
--max-tokens N         max tokens to generate
--timeout SECONDS      response timeout, 0 = wait forever (default 300)
--reasoning LEVEL      none|minimal|low|medium|high (sent as reasoning_effort)
--history-percent N    chat history share of the screen (50-95)
--context-window N     total context tokens for the meter (default: endpoint's)
--context-mode MODE    pause|rolling|compact when the window fills
--stream               show replies token by token (--stream / --no-stream)
--save-dir DIR         where /save and autosave write files
--save-format FMT      json|markdown
--autosave             auto-save after every reply (--no-autosave to force off)
-r, --resume [FILE]    resume a saved session; no FILE = pick from save dir
--campaign FILE        campaign to play as (system prompt, lore, greeting, PC)
--config PATH          use a specific config file
--version              show version
```

Every simple config key has a matching flag; the structured keys
(`labels`, `theme`, `keys`, `knowledge`) live in the config file or the
`/config`/`/knowledge` screens only.

Environment variables (overridden by CLI flags):
`SEKKA_ENDPOINT`, `SEKKA_MODEL`, `SEKKA_API_KEY`, `SEKKA_CONFIG`,
`SEKKA_CAMPAIGN`. A key given by flag or env var is used for the session but
never written to your config file.

Configuration precedence: **CLI flags > environment > campaign file > config
file > defaults** - so one campaign can be pointed at any endpoint.

## Slash commands

| Command    | What it does                                                |
|------------|-------------------------------------------------------------|
| `/help`      | show commands and current key bindings                      |
| `/save`      | save history to `sekka_YYYYMMDD_HHMMSS.json` (asks first)   |
| `/config`    | open the configuration screen (saved to the config file)    |
| `/models`    | pick a model from the endpoint                              |
| `/knowledge` | manage knowledge files offered to the model as tools        |
| `/campaign`  | show the loaded campaign, or load one: `/campaign FILE`   |
| `/thinking`  | show/hide model thinking & tool calls (also **ctrl+t**)     |
| `/stop`      | stop the reply in flight (also **ctrl+x**)                  |
| `/undo`      | delete the last exchange (your message and the reply)       |
| `/edit`      | put your last message back in the input to fix and resend   |
| `/regen`     | replace the last reply with a new one (the old one is kept) |
| `/swipe`     | cycle through the other generated versions of that reply    |
| `/clear`     | clear the on-screen and sent chat history                   |
| `/exit`      | quit (also **ctrl+c twice**)                                |

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
| `ctrl+c`    | quit - press twice within 2 s (copies a selection if one exists) |
| `ctrl+t`    | show/hide model thinking & tool calls |
| `ctrl+x`    | stop the reply in flight (keeps what arrived) |
| `escape`    | clear the input box             |

Thinking and tool-call lines are hidden by default and always start hidden;
`/knowledge` files become read-only tools for models that support tool
calling (see [docs](docs/configuration.md) for the security notes).

## Files

- Config: `./.sekka/config.json`, then `~/.sekka/config.json` (first one found
  wins; `/config` writes back to it). See
  [docs/configuration.md](docs/configuration.md).
- Campaign: `campaign.json` beside the config (or `--campaign FILE`) - the
  scenario itself: system prompt, labels, lore, opening scene, your character.
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

*sekka* 雪華: each chat crystallises one exchange at a time.

Built by [Qwen 3.8 Flash Next](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4). ⚡🇨🇦

## License

MIT - see [LICENSE](LICENSE).
