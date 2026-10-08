# Sekka

A lightweight terminal (TUI) chat client for any **OpenAI-compatible** LLM
endpoint (vLLM, llama.cpp server, Ollama's OpenAI API, LM Studio, ...).
Built for lightweight model exploration, simple text knowledge-base chat
bots, and role playing games. It runs in your terminal, and `sekka serve`
can put the identical screen in a browser instead.

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
- **Lorebook triggers**: give a knowledge file keywords (or mark it *always*)
  and it is pulled into context the moment the topic appears in your message -
  no tool calling needed, no extra round trip, works on any model
- **Pinned state**: `/note` keeps a short block (inventory, injuries, promises,
  "the door is still barred") in every request, so it survives summarising and
  long scenes; `/note +text` appends and it saves itself into your campaign
- **Real dice**: `/roll 2d6+3` rolls from `os.urandom`, shows the individual
  dice, and puts the result in the model's context so it cannot fudge the outcome
- **Out of character**: `/ooc <text>` for one message, `ctrl+o` to toggle it for
  everything you type until you switch it back
- Sampler control: `top_p`, `min_p`, penalties and stop sequences - sent only
  when you set them, so strict endpoints never see an unknown key
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
- **Browser mode** (`sekka serve`): the same terminal session, streamed to a
  page - not a rewritten web front end

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
# or pin a model too:
sekka --endpoint http://your-llm-server:8000/v1 --model your-model-id
```

That flag is written to `./.sekka/config.json` for you, so from now on you start a
chat in this directory by typing just **sekka**. Only `--endpoint` and `--model`
are remembered - never `--api-key` - and `--no-remember` opts out. If you keep a
`~/.sekka/config.json`, a project's endpoint still goes in the project.

Type and press **enter**. `/help` lists the in-chat commands.

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
--top-p FLOAT          nucleus sampling (unset = endpoint default)
--min-p FLOAT          min-p sampling (vLLM/llama.cpp extension)
--presence-penalty F   presence penalty
--frequency-penalty F  frequency penalty
--repetition-penalty F repetition penalty (vLLM/llama.cpp extension)
--stop SEQ             stop sequence; repeat the flag for several
--remember             remember --endpoint/--model (--remember / --no-remember)
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
| `/note`      | show / set / `+append` / clear the pinned running state    |
| `/roll`      | roll real dice, e.g. `/roll 2d6+3` (goes into context)     |
| `/play`      | roleplaying quick reference (`/rp` for short)             |
| `/ooc`       | say something out of character, as one message            |
| `/campaign`  | show the loaded campaign, or load one: `/campaign FILE`   |
| `/thinking`  | show/hide model thinking & tool calls (also **ctrl+t**)     |
| `/stop`      | stop the reply in flight (also **ctrl+x**)                  |
| `/undo`      | delete the last exchange (your message and the reply)       |
| `/edit`      | put your last message back in the input to fix and resend   |
| `/regen`     | replace the last reply with a new one (the old one is kept) |
| `/swipe`     | cycle through the other generated versions of that reply    |
| `/clear`     | clear the on-screen and sent chat history                   |
| `/exit`      | quit (also **ctrl+c twice**)                                |

## Browser mode

`sekka serve` shows the *same* screen in a browser:

```bash
pip install 'sekka[serve]'          # or: pipx inject sekka textual-serve
sekka serve                         # http://127.0.0.1:8484
sekka serve --serve-port 9000
sekka serve -- --endpoint http://box:8000/v1    # flags after -- reach each session
```

It is not a second front end. Each browser tab runs a real `sekka` process on a
pseudo-terminal and the terminal rendering is streamed to the page, so commands,
keys, themes, streaming and saves behave exactly as they do in a terminal.

Before relying on it:

- **Every tab is its own chat.** Tabs share the config file, the campaign file
  and the save directory, so two tabs can write the same files (last save wins).
- **The browser keeps its shortcuts.** `ctrl+t` and similar may be eaten by the
  browser; the slash commands (`/thinking`, `/ooc`, `/stop`, `/play`) always work.
- **No auth, no TLS.** Binding is loopback-only by default and sekka refuses a
  non-loopback host unless you pass `--serve-allow-public`. If you do, what you
  probably want is an SSH tunnel (`ssh -L 8484:127.0.0.1:8484 host`) or a reverse
  proxy with auth - anyone who reaches the page can use your endpoint and read
  whatever is on screen.
- Config discovery follows the **server's** working directory, so run
  `sekka serve` from the folder whose campaign you want to play.

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
| `ctrl+o`    | out-of-character mode on/off |
| `escape`    | clear the input box             |

Thinking and tool-call lines are hidden by default and always start hidden;
`/knowledge` files become read-only tools for models that support tool
calling (see [docs](docs/configuration.md) for the security notes).

## Roleplaying cheat sheet

Printed in-app by **`/play`** (alias `/rp`):

```
/roll 1d20+3        real dice; the result rides along with your next message
/note +took the key append to the pinned state (survives summarising)
/ooc shorter scenes one out-of-character message  (ctrl+o toggles the mode)
/regen              a different reply to the same line, the previous one kept
/swipe              cycle the versions you generated
/undo               drop the last exchange          /edit   fix and resend it
/stop               stop the reply mid-sentence, keep what landed   (ctrl+x)
/campaign FILE      load a campaign (a campaign.json beside the config loads itself)
```

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
