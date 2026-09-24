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

### Prompt/context caching

Nothing special is needed from sekka: vLLM (automatic prefix caching),
llama.cpp, SGLang and hosted APIs cache the shared prefix of consecutive
requests on their own, and sekka keeps the prompt prefix stable
(append-only history, unchanged system prompt) so those caches hit. Note that
`rolling`/`compact` modes change the prompt prefix when they trigger, so the
next request after a roll/compact re-pays the prefill cost.

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
