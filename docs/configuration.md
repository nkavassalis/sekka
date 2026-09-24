# Sekka configuration

## Where the config lives

Sekka looks for a JSON config file in this order (first hit wins):

1. `--config PATH` flag, or the `SEKKA_CONFIG` environment variable
2. `./.sekka/config.json` (current working directory)
3. `~/.sekka/config.json` (user home)

The `/config` screen edits these values and writes them back to whichever
file was loaded — or to `./.sekka/config.json` if no file existed yet.

Precedence of a single value: **CLI flag > environment variable > config file >
built-in default**.

## Full example

```json
{
  "endpoint": "http://10.1.13.99:8000/v1",
  "model": "qwen3.8-flash-next",
  "api_key": "",
  "system_prompt": "You are a helpful assistant.",
  "temperature": 0.7,
  "max_tokens": null,
  "request_timeout": 300,
  "history_percent": 80,
  "context_window": null,
  "context_mode": "pause",
  "reasoning": "medium",
  "knowledge": [
    {"file": ".sekka/credit_card_processing.md",
     "description": "Full policy for credit card processing at the clinic",
     "enabled": true}
  ],
  "autosave": false,
  "save_dir": ".",
  "save_format": "json",
  "labels": {"user": "You", "assistant": "Assistant"},
  "theme": {
    "user": "cyan",
    "assistant": "magenta",
    "system": "yellow",
    "stats": "grey58",
    "error": "red"
  },
  "keys": {
    "submit": "enter",
    "newline": "alt+enter",
    "scroll_up": "pageup",
    "scroll_down": "pagedown"
  }
}
```

## Keys explained

Every key below except `labels`, `theme`, `keys` and `knowledge` also has a
CLI flag (README usage lists them; flags win over the config file). The
structured keys are managed in the config file or the `/config` and
`/knowledge` screens.

| Key               | Type            | Default                       | Meaning                                            |
|-------------------|-----------------|-------------------------------|----------------------------------------------------|
| `endpoint`        | string          | `http://localhost:8000/v1`    | Base URL of an OpenAI-compatible server (no trailing `/chat/completions`) |
| `model`           | string          | `""` (pick from endpoint)     | Model id. Empty means sekka lists `/models` and asks |
| `api_key`         | string          | `""`                          | Sent as `Authorization: Bearer ...` if non-empty   |
| `system_prompt`   | string          | "You are a helpful assistant." | Sent as a leading `system` message on every request |
| `temperature`     | number/null     | `0.7`                         | `null` omits the parameter entirely                |
| `max_tokens`      | int/null        | `null`                        | `null` omits the parameter                         |
| `request_timeout` | seconds/null    | `300`                         | Read timeout per request; `0`/`null` waits forever (connect timeout is fixed at 10 s) |
| `history_percent` | int (50-95)     | `80`                          | Share of the screen for the chat history; the rest goes to the input editor |
| `context_window`  | int/null        | `null` (= endpoint's `max_model_len`) | Total context size in tokens shown by the meter; also forces the full-context behaviour |
| `context_mode`    | `pause`/`rolling`/`compact` | `pause`         | What happens when the context window fills up (see below) |
| `reasoning`       | `none`/`minimal`/`low`/`medium`/`high` | `medium` | Reasoning effort sent to thinking models as `reasoning_effort`; `none` omits the parameter for models that don't support it |
| `knowledge`       | list of entries | `[]`                          | Files offered to the model as read-only tools (see "Knowledge files") |

### Prompt/context caching

Nothing special is needed from sekka: vLLM (automatic prefix caching),
llama.cpp, SGLang and hosted APIs cache the shared prefix of consecutive
requests on their own, and sekka keeps the prompt prefix stable
(append-only history, unchanged system prompt) so those caches hit. Note that
`rolling`/`compact` modes change the prompt prefix when they trigger, so the
next request after a roll/compact re-pays the prefill cost.

### Knowledge files (`/knowledge`)

`/knowledge` opens a screen where you list files plus descriptions and tick
which ones are active. Each **enabled** entry is offered to the model as one
read-only tool (one tool per file, named after the file). Use it for process
docs ("credit card processing", "insurance verification"), character cards,
location lore for RPGs - anything the model should be able to look up.

- **Requires a tool-calling model.** Models without tool support simply ignore
  the tools (or error, depending on the server). Reasoning-only or plain
  models won't use knowledge files.
- **Entries are disabled until you tick them**, and the enabled/disabled state
  is saved to the config file immediately - restarting sekka in the same
  directory with the same config remembers what was on.
- **The description is the sales pitch.** The model decides whether to call a
  tool purely from its description. Write what is in the file and *when* to
  reach for it: "Full step-by-step policy for credit card processing at the
  clinic - call this before answering any payment question" beats "policy doc".
- **Security - no arbitrary file reads.** sekka registers one fixed tool per
  listed file and the tools take **no parameters**. The model can only trigger
  reads of files you listed and enabled; it cannot name a file, pass a path,
  or influence which file is read in any way. sekka itself does the reading
  (UTF-8, max 256 KB) and feeds the contents back as the tool result.
  Unknown/hallucinated tool names are refused without touching the disk.

### Thinking & tool calls (`/thinking`, `ctrl+t`)

Reasoning content (`reasoning_content`/`reasoning` in the API response) and
tool-call activity are recorded but **hidden by default** and always hidden at
startup. `ctrl+t` or `/thinking` toggles the overlay; it also
shows/hides the thinking and tool lines from **earlier turns** already on
screen. Nothing about the display mode changes what is sent to the model or
what `/save` writes.

### What happens when the context window fills

The bottom-right meter shows the conversation size (exact token counts from the
API when available, otherwise a ~4-chars-per-token estimate). When a new
message would run past the window (a ~10% reply reserve is kept), sekka acts
according to `context_mode`:

- **`pause`** (default) — refuses the message and suggests `/save` + `/clear`
  or switching modes. Nothing is ever silently lost.
- **`rolling`** — drops the oldest turns (always keeping the current exchange)
  and notes how many were dropped.
- **`compact`** — asks the model to summarize the older turns into a single
  system message, keeps the recent tail, and continues. The on-screen history
  is not rewritten, only what gets sent to the model. If the summary request
  fails, sekka falls back to rolling for that turn.
| `labels.user`     | string          | `"You"`                       | Name shown before your messages (1–30 chars)       |
| `labels.assistant`| string          | `"Assistant"`                 | Name shown before replies (also editable in `/config`) |
| `save_dir`        | string          | `"."`                         | Where `/save` and autosave write files             |
| `save_format`     | `json`/`markdown` | `json`                      | `.json` or `.md` output                            |
| `autosave`        | bool            | `false`                       | Write a timestamped file after every reply (still keeps confirm-before-write for manual `/save`) |

### `theme`

Colors accept anything [rich](https://rich.readthedocs.io/) understands:
`"cyan"`, `"magenta"`, `"grey58"`, `"bright_green"`, `"#ff8800"`, ...

- `user` — your messages
- `assistant` — model replies
- `system` — sekka's own notices and the input border
- `stats` — the dim `[time, tok/s]` line after each reply
- `error` — error notices

Invalid colors are rejected at startup with a clear message.

### `keys`

Textual key-binding names (`enter`, `alt+enter`, `pageup`, `ctrl+s`, ...).

- `submit` — send the current input
- `newline` — insert a newline instead of sending
- `scroll_up` / `scroll_down` — page the chat history

Caveat: bindings are intercepted in the input editor, which also owns keys
like `ctrl+e`, `ctrl+k`, `ctrl+w`, pageup/pagedown for its own cursor. If you
rebind to one of those, sekka's binding wins and the editor loses that
shortcut. `alt+enter` requires a terminal with enhanced key support (most
modern ones: kitty protocol, wezterm, iTerm2, Windows Terminal, foot); on
older terminals it may arrive as a plain enter.

## Saved files

`/save` confirms first, then writes `sekka_YYYYMMDD_HHMMSS.json` (collision
safe: `..._1.json`, ...) into `save_dir`:

```json
{
  "saved_at": "2026-02-14T10:15:30",
  "messages": [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi"}
  ]
}
```

`save_format: "markdown"` produces readable `sekka_...md` instead.
