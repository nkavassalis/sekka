# Sekka configuration

## Where the config lives

Sekka looks for a JSON config file in this order (first hit wins):

1. `--config PATH` flag, or the `SEKKA_CONFIG` environment variable
2. `./.sekka/config.json` (current working directory)
3. `~/.sekka/config.json` (user home)

**Remembered flags:** pass `--endpoint` and/or `--model` and sekka writes them into
`./.sekka/config.json` at startup, so the next bare `sekka` in that directory
just works. Deliberately narrow:

- only `endpoint` and `model` - never `api_key` or anything else;
- never a value that came from an environment variable;
- never into `~/.sekka/config.json`: a project's endpoint belongs to the project,
  so a local file is created instead (and wins on the next load);
- never rewritten when the file already holds those values;
- opt out with `--no-remember` or `"remember": false`.

Choosing a model from the `/models` picker is remembered too, because you chose
it; auto-selecting the one model an endpoint happens to host is not.

The `/config` screen edits these values and writes them back to whichever
file was loaded — or to `./.sekka/config.json` if no file existed yet.

Precedence of a single value: **CLI flag > environment variable > campaign file
> config file > built-in default**.

## Full example

```json
{
  "endpoint": "http://your-llm-server:8000/v1",
  "model": "your-model-id",
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
  "stream": true,
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

### Campaigns (`--campaign FILE`, `SEKKA_CAMPAIGN`, `campaign`)

A campaign is the *scenario*, kept separate from where your model lives, so a
whole game is one folder you `cd` into and run Sekka in. It is searched for in
this order: `--campaign` / `SEKKA_CAMPAIGN`, the `campaign` key in your config
file (relative to that file), `campaign.json` beside the config, then
`./.sekka/campaign.json`.

```json
{
  "name": "The Frostspire Marches",
  "system_prompt": "You are the game master ...",
  "labels": { "user": "Player", "assistant": "GM" },
  "temperature": 0.9,
  "reasoning": "low",
  "player": "Vesna Chalk, hedge-mage, 12 shillings, a borrowed coat",
  "greeting": "The last barge of the night slides into the locks. What do you do?",
  "knowledge": [
    { "file": "knowledge/world.md", "description": "World rules and factions", "enabled": true }
  ]
}
```

- Campaign values **override the config file** but not CLI flags or environment
  variables, so one campaign runs against any endpoint.
- They are never written back into `config.json` by `/config` or `/knowledge` —
  edit the campaign file itself. `/campaign FILE` records only the *path*, so the
  same scenario loads next time.
- `greeting` opens the scene: it is displayed and enters the model's context, so
  what you type first is an answer to it.
- `player` is appended to the system prompt as the player's character.
- Relative `knowledge` paths resolve against the campaign's folder first, then
  the working directory, so a campaign folder works from anywhere.
- `/save` records which campaign (and role labels) a session used, and
  `sekka -r` reloads them so a saved game resumes as itself. Markdown saves stay
  plain and carry no metadata.

### Streaming (`--stream` / `--no-stream`, `stream`)

Replies stream into the view token by token (default on). Endpoints that
ignore `stream=true` are handled: sekka falls back to showing the whole reply
at once. **`ctrl+x`** (or `/stop`) aborts a reply in flight and keeps the text
that already arrived, marked `[stopped]`; it also frees the editor immediately
when the endpoint has gone quiet, though an abandoned request may still be
finishing server-side. Streaming is skipped for the `/compact` summary call.

### Dice, out-of-character messages, and samplers

**`/roll 2d6+3`** - dice come from `random.SystemRandom` (`os.urandom`), the
individual throws are shown (`2d6+3 = 9 (4, 2) +3`), and the result is queued to
ride along **in front of your next message**, where the model can see it. That
placement is deliberate: the number arrives with the action it belongs to, and
the model cannot quietly invent a better one. Supports `d20`, `2d6`, `3d6+2`,
`2d8-1d4+3`; a bare `/roll` explains the syntax.

**`/ooc <text>`** wraps one message as `(OOC: ...)`; **`ctrl+o`** toggles a mode
where everything you type is wrapped until you switch it back (the status row
shows `OOC mode on`). It is still an ordinary user turn, so the model can answer
it, and it saves like any other line.

**Samplers**: `top_p`, `min_p`, `presence_penalty`, `frequency_penalty`,
`repetition_penalty`, `stop` (list of up to 8 sequences). All default to *unset*,
and unset means the key is **not sent at all** - `min_p` and `repetition_penalty`
are vLLM/llama.cpp extensions a strict OpenAI endpoint would reject, so sekka
never volunteers them. Each has a CLI flag (`--top-p`, `--stop`, ...) and a field
in `/config`. A campaign may carry them too, so a scenario can ask for a hotter
sampler and get it.

### `/save` vs `/export`

`/save` writes into `save_dir` on the machine running sekka - which under
`sekka serve` is the server, not the viewer. `/export` delivers the same document
to the *player*: under textual-serve it is a real browser download through a
single-use URL on the same port, and in a terminal it goes to your downloads
folder (Textual's own delivery API, so one command covers both).

- Format follows `save_format` (JSON is resumable with `sekka -r`; markdown is for reading).
- Contents are exactly the visible turns. No system prompt, no campaign path, no
  pinned note - so `/export` is allowed in read-only mode and `save_dir` never
  has to be writable.
- The file is streamed by the app process, so closing the tab before it lands
  cancels the download; delivery failures are reported in the chat log.

### Read-only mode (`readonly`, `--readonly`, `serve --serve-readonly`)

For sessions you did not configure yourself - a served tab given to another
player, a shared machine, someone else poking your endpoint. Everything about
*playing* keeps working: chat, `/roll`, `/ooc`, `/note`, `/undo`, `/edit`,
`/regen`, `/swipe`, `/stop`, `/thinking`, `/play`. What is refused:

| Blocked | Why |
| --- | --- |
| `/config` (form) | replaced by a summary; the API key is never shown |
| `/models` | picking a model writes the config file |
| `/knowledge` | edits lore, and lore is prompt content |
| `/campaign <file>` | a different campaign is a different system prompt |
| `/save`, autosave | read-only means no files written |
| resume picker (`-r`) | it lists everyone's saved sessions |
| remembering `--endpoint`/`--model` | no writes |

`/note` is allowed and kept in memory only - it does not touch the campaign file,
and the session tells you so. A host-specified `--resume FILE` is honoured: that
was the host's own choice.

### Browser server (`sekka serve`)

`sekka serve` needs the optional extra: `pip install 'sekka[serve]'`, or
`pipx inject sekka textual-serve` for a pipx install. It serves the identical
terminal UI on `http://127.0.0.1:8484`, using `serve_host` / `serve_port` from
the config or `--serve-host` / `--serve-port`.

- **The `--serve-*` flags need the `serve` mode.** `sekka --serve-port 9100` exits
  2 with a hint instead of opening the terminal UI and dropping the flag on the
  floor (it used to do exactly that, and `--serve-host`/`--serve-port` would even
  reach the config overrides). The check runs before the config is loaded, so a
  refusal also can't write anything with `--remember`.

- **Loopback by default, and enforced.** A non-loopback `serve_host` makes sekka
  exit with an explanation; `--serve-allow-public` overrides that. The server has
  no authentication and no TLS, so a reachable port means anyone can use your
  endpoint and read the transcript. For remote use prefer an SSH tunnel
  (`ssh -L 8484:127.0.0.1:8484 host`) or a reverse proxy with auth.
- **Per-tab processes.** Each browser tab spawns its own `sekka` (config is read
  per session, so config changes appear on the next tab). Sessions share the
  config, campaign and save files.
- **Flags after `--` are forwarded** to each spawned session:
  `sekka serve -- --endpoint http://box:8000/v1`. Tokens are shell-quoted before
  the server sees them, so odd characters stay data.
- Config discovery uses the server process's working directory: `cd` into the
  campaign folder first.
- Browser-reserved chords (`ctrl+t`) may not reach the app; the slash-command
  equivalents always do.

### The pinned note (`note`, `/note`)

`note` is a short block of text appended **last** in the system prompt, so the
model weighs it most: inventory, injuries, promises made, "the door is still
barred from the scene three ago". It is the one thing that survives `compact`
summarising your history away.

```json
{ "note": "PC: Vesna. Carrying: brass key, 12 shillings. Owes Pock a favour." }
```

Edit it three ways, all of which persist it:

- `/note` shows it, `/note <text>` replaces it, `/note +<text>` appends a line
  (useful as the scene moves), `/note clear` empties it.
- `/config` has the same field.
- With a campaign loaded it is written into the **campaign file**, not
  `config.json` - the campaign layer overrides the config file, so writing there
  would be ignored next start. Any campaign-owned value you change in `/config`
  (system prompt, labels, temperature, ...) is written back to the campaign the
  same way, preserving other keys in that file.
- `/save` records the note as it was at that moment, and `sekka -r` restores
  *that* version, so a saved game picks up with the state it had when you saved -
  even if the campaign file has since been edited.

### Lore that arrives on its own (`keywords`, `always`)

A knowledge entry can carry two optional fields:

```json
{
  "file": "knowledge/seraine.md",
  "description": "Character card for Seraine Ashfoot",
  "enabled": true,
  "keywords": ["seraine", "tavern", "last hearth"]
}
```

- **`keywords`** - the moment one appears in your message (case-insensitive
  substring match), the file is read and appended to the system prompt, for that
  turn and every later one. Sekka sends **one** request: the model never has to
  think about looking it up, so this works on models without tool calling and
  skips the tool round trip entirely. The status row shows `lore loaded: …` so
  you can see the prompt grow.
- **`always`** - skip the lookup: the file goes into context before the first
  message. Use it for the rules of the world; use keywords for the cast.
- A file is loaded at most once per session, whichever way it arrived, and
  triggered files stop being offered as tools. Everything folded into the prompt
  is capped at 60k characters, oldest entry first.
- Keywords are matched against **your** messages only - the model cannot steer
  what gets loaded, and it can still only ever read files you listed.

### Turning the clock back (`/undo`, `/edit`, `/regen`, `/swipe`)

Ate a wrong turn mid-scene? `/undo` deletes the last exchange, `/edit` puts your
last message back in the input to fix and resend, and `/regen` asks for a
different reply to the same prompt. `/regen` **keeps** every version it
generated, and `/swipe` cycles through them; the version you are looking at is
what gets saved and sent as context next, so you can audition openings and
decide later. These edit the chat log, so autosave/`/save` follow them; the
on-screen log is redrawn from the saved history after each one.

### If a reply comes back empty

Reasoning models can spend your whole `max_tokens` budget on thinking and leave
no room for the answer. Sekka does not store a blank assistant turn in that case:
it keeps your message and says why (`Empty reply (the model spent the whole reply
budget on thinking - raise max tokens, or set reasoning to none)`). Leave
`max_tokens` unset, or well above a few hundred, when using a thinking model.

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
| `campaign`        | string          | `""`                          | Campaign file to load (relative to this config file) |
| `greeting`        | string          | `""`                          | Opening scene (usually from a campaign); shown + in context |
| `player`          | string          | `""`                          | Your character, appended to the system prompt |
| `note`            | string          | `""`                          | Pinned running state, sent last; `/note` edits it |
| `remember`        | bool            | `true`                        | Write `--endpoint`/`--model` into the local config file |
| `readonly`        | bool            | `false`                       | Play only: no settings edits, no file writes |
| `serve_host`      | string          | `"127.0.0.1"`                 | Bind address for `sekka serve` |
| `serve_port`      | int             | `8484`                        | Port for `sekka serve` |
| `top_p`             | float \| null   | `null` (endpoint)             | Nucleus sampling cutoff (0-1) |
| `min_p`             | float \| null   | `null` (endpoint)             | min-p cutoff (vLLM / llama.cpp extension) |
| `presence_penalty`  | float \| null   | `null` (endpoint)             | Presence penalty (-2 to 2) |
| `frequency_penalty` | float \| null   | `null` (endpoint)             | Frequency penalty (-2 to 2) |
| `repetition_penalty`| float \| null   | `null` (endpoint)             | Repetition penalty (0-2, vLLM / llama.cpp) |
| `stop`              | list of strings | `[]`                          | Stop sequences (up to 8) |
| `stream`          | bool            | `true`                        | Stream replies into the view; `--no-stream` shows them whole |
| `autosave`        | bool            | `false`                       | Update one session file after every reply (first write picks the timestamped name; manual `/save` still asks first) |

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

`/save` (and autosave) write timestamped files; JSON saves are also sessions.

Secrets: a key supplied via `--api-key` or `SEKKA_API_KEY` is used for the
session but **not** written to the config file by `/config` or `/knowledge`
(the file keeps whatever it already had). Typing a key into `/config` does
persist it, so only do that where the file is readable.
`sekka -r FILE` resumes one, and bare `sekka -r` lists the JSON files in the
current `save_dir` newest-first. Resume files are treated as **untrusted
input**: size-capped read, strict JSON, message/role/content type and size
limits, and only `role`/`content` of `user`/`assistant`/`system` messages are
loaded - anything else raises a readable error and the app continues fresh.
A colon (or any non `[\w-]` char) in a saved-JSON position can't hurt the
app because unknown keys are dropped rather than rendered.

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
