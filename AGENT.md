# AGENT.md — maintainer notes for sekka

**Project mode: REAL** (public GitHub repo under `nkavassalis`, full test suite
must pass before any push, README + AGENT.md kept current).

Hard-won context for anyone (human or agent) working on this repo. Ordered by
"you will hit this soonest."

## Ground rules (owner preferences)

- **Never cut releases or git tags unless the owner explicitly says so.**
  Building/verifying artifacts locally is fine.
- **`CNAME` must stay in the commit** or sekka.org stops resolving via Pages.
  `.nojekyll` must stay too.
- `.sekka/` is gitignored — never commit the local dev config.

## Textual gotchas (each cost real debugging time)

- **No `right:`/`bottom:` CSS properties**, and `offset:` keyword corners are
  limited. To pin something to a corner, use a `dock: bottom` row inside the
  container plus `text-align: right`. The context meter + notices live in
  `#status_row` (docked bottom of `#input_panel`) for exactly this reason.
- **`border_subtitle` positioning is dictated by border style**, not by you,
  and is painful to verify headless. Don't fight it; use docked widgets.
- `ModalScreen` does **not** auto-dismiss on Escape (it used to). Every modal
  (ConfigScreen, ConfirmScreen, ModelScreen) has its own priority-bound
  `escape` handler — keep it when editing modals.
- Priority bindings are how key overrides work: child `BINDINGS` with
  `priority=True` beat parent ones (ChatInput owns arrows/enter; app-level
  PageUp/PageDown scroll history).
- `Widget.scroll_to_widget(widget, top=True, immediate=True)` — there is no
  `visible=` kwarg.
- Default `ctrl+c` is *copy*, not quit; `ctrl+q` quits.
- History/input split is driven by CSS variables `$sekka-history-fr` /
  `$sekka-input-fr` computed from `history_percent` — don't hardcode fractions.
- In tests, `widget.render_lines(Region)` returns `Strip`s for the *content*
  box only; borders live in the compositor. Verifying border text headless is
  a rabbit hole — assert on widget `.region` and `.update()`d text instead.
- Bad CSS in `App.CSS` surfaces as a `StylesheetParseError` during
  `run_test`, which looks like "app boots but history is empty" in 17 tests.
  Read the CSS error first when many TUI tests fail at once.
- **Textual 8 API sharp edges** (each broke a first attempt at /knowledge):
  - `TextArea` uses `.text`, NOT `.value`; selection check is
    `widget.selection is not None and not widget.selection.is_empty`.
  - `styles.display` accepts `"block"/"none"` strings, not bools.
  - `styles.color` accepts a color NAME string (`"green"`), not a
    `rich.color.Color` object (it comes back as `textual.color.Color`).
  - `Checkbox.Changed` has no `.button`/`.sender` — use
    `getattr(event, "toggle_button", None)`.
  - A method containing `yield` is a generator — the `with Horizontal(...):
    yield widget` compose idiom does NOTHING in a normal method like
    `_refresh_list()`; build widgets and `mount()` them explicitly.
  - Widget IDs must be unique per screen — two `Horizontal(id="k_form_row")`
    raise MountError. And IDs must match `[A-Za-z0-9_-]+`: `id=f"model:{name}"`
    crashed the DOM for models named like `org/model:v1` (latent bug, fixed
    with index-based ids `model-item-{i}`). Never build widget ids from
    externally-controlled strings.
  - In tests, drive ListView picks with `pilot.press("enter")` (modal
    auto-focuses the list); ListItem has no `action_select`.
  - `DirectoryTree` + `on_directory_tree_file_selected` makes a fine minimal
    file browser (FileBrowseScreen starts in `.sekka` when it exists).
  - `Screen` objects have **no** `push_screen` in Textual 8 — push from a
    screen via `self.app.push_screen(...)` (KnowledgeScreen's browse button
    crashed in real use over this; test covers it).
  - App exit state is `app._exit` (private); `App.exit()` sets it.
- ctrl+c is a **priority app binding**: copy must be handled manually
  (selection check + `action_copy()`) because priority steals it from TextArea.

## Bugs that were fixed — don't reintroduce

- **Replies arrive with leading newlines** (chat templates). Display trims
  with `strip("\n")`; the model context and saves keep the raw content.
- **Preset model skipped `/models`**, so the context meter showed `?`. Fixed
  with a silent background `_fetch_context_size` at mount and after `/config`.
- **Rolling/compact mutate `chat` only.** `full_chat` is the visible-everything
  log; `/save` and autosave MUST use `full_chat`. Test helpers that fake past
  turns must append to both lists (this bit us once).
- **requests timeout is a `(connect, read)` tuple** — `sekka.client._timeout`
  builds `(10.0, read or None)`; `0`/`None` means wait forever. Default 300 s.
- vLLM reports context size as **top-level `max_model_len`** in `/models`
  (`ModelInfo` dataclass captures it); `context_window` config overrides.
- Endpoints that return **HTML instead of JSON** (captive portals, a web UI on
  the API port) now raise a clear ClientError via content-type check.
- Context meter math: `used = max(exact API usage, char/4 estimate incl.
  system prompt)`; the send guard reserves ~10% of the window for the reply.
- Pause mode must not mutate `chat` (it rolls back the user turn on errors —
  same rollback pattern as API failures).

- **Turn editing (`/undo`, `/edit`, `/regen`, `/swipe`) rebuilds the visible log
  from `full_chat`** via `_rebuild_history()` instead of surgically removing
  widgets: fewer states to get wrong, and it mounts in one batch, which also
  fixed slow session resume (previously one `mount()` + `scroll_end()` per
  message). Consequences to preserve:
  - `_display_text()` is the single source of truth for how a stored message
    looks; `_append()` and `_rebuild_history()` must stay in agreement.
  - Stats/timing lines and autosave notes are *display-only*: they live in
    `ui_lines` and disappear on rebuild. Accepted (matches how saves work).
  - `turn_alts` holds every generated version of the last reply (index
    `alt_index`); `/regen` pops the assistant turn, sets `_regen_pending`, and
    the worker appends the new text. The visible reply is always what is in
    `chat`/`full_chat`, so saves and the next request follow a swipe.
  - Turn edits are refused while `busy` (stop with ctrl+x first).

## Decisions (and why)

- **Textual** over prompt_toolkit/urwid: scrollback + TextArea + CSS.
- **Non-streaming**: wall-clock timing + authoritative `usage` tokens is
  simpler; spinner shows `❄  Ns` (glyph cycles ❄❅❆❅, two spaces, no dots).
- **Context-full modes**: default `pause` — silent loss is worse than a
  refusal. `rolling` drops oldest; `compact` LLM-summarizes old turns into a
  system message and falls back to rolling if the summary call fails. On-screen
  history is never rewritten; only the sent context changes. Notices go to the
  status row, not the chat.
- **Prompt caching needs no client work**: backends (vLLM APC, llama.cpp,
  hosted APIs) cache on identical prompt prefix; our prompt is append-only.
  `rolling`/`compact` invalidate the prefix when they trigger — documented,
  accepted.
- **No compiled binaries.** Nuitka standalone measured ~149 MB unpacked /
  48 MB tarball for a pure-Python I/O-bound app — zero speed benefit, terrible
  artifact size. Owner vetoed. Distribution = `pipx install git+...`.
- **Knowledge files as tools**: one tool per enabled file, **zero-parameter
  schemas** — the model can only trigger reads of files listed+enabled in the
  config, never name a path; sekka does the reading (UTF-8, 256 KB cap), and
  hallucinated tool names are refused without touching disk. Entries persist
  in `.sekka/config.json` enabled/disabled; disabled entries are remembered
  but not offered. Requires tool-calling models (documented). Tool loop caps
  at 8 rounds.
- **Thinking/tool lines** (`msg-reasoning`/`msg-tool` Statics) are appended
  always but `display:none`d; `/thinking`+`ctrl+t` flips styles on the live
  widgets, so earlier turns toggle too. Always off at startup (owner rule).
- **Quit is ctrl+c twice** (2 s window); first press copies if a selection
  exists. Escape clears the input. Both also in /help — keep /help, README
  and docs/configuration.md key tables in sync when bindings change.
- **reasoning_effort**: config `reasoning` (default `medium`), omitted from
  the payload when `none` so non-reasoning endpoints never see it. The
  /compact summary call always uses `reasoning_effort="none"`.
- **Resume/session files are untrusted**: `storage.load_history` size-caps,
  strictly validates shape/roles/content types+limits, and copies only
  role/content — never render or store whatever a JSON file contains.
  `-r` with no value = picker listing `save_dir` JSON newest-first.
- Saves: timestamped files (autosave rewrites one file per session),
  confirm-before-write, no autosave by default.

## Deployment (sekka.org) — internals live in LOCAL.md

Deploy/DNS/CDN specifics (distribution ID, hosted zone ID, AWS profiles, cert
region, invalidation command) and the dev endpoint hostname were **moved out of
this public file into `LOCAL.md`, which is gitignored**. Never move them back,
and never add hostnames, private IPs, account or resource IDs here: this repo
is public, and tracked content is public forever. Describe infra abstractly
("the dev vLLM box", "the CDN") and put real values in `LOCAL.md` or env vars.
The CNAME/`.nojekyll` rules above still apply.

## Config-persistence rule (new)

`Config.persistable()` is what gets written to disk: keys overridden by CLI
flags or env vars keep their file value (or are omitted if the file had none),
so `SEKKA_API_KEY`/`--api-key` can never be silently persisted by /config or a
/knowledge toggle. When the user edits such a key in /config, `release_override`
is called for changed keys only. Don't simplify this back to writing
`config.values`.

- **Streaming is the default** (`stream: true`, `--stream/--no-stream`).
  `client.stream_chat_completion()` yields cumulative-progress `StreamEvent`s;
  the TUI mounts the assistant `Static` on the first token (which also kills the
  snowflake spinner) and `.update()`s it. Points not to undo:
  - The generator runs in a thread (`asyncio.to_thread`) feeding an
    `asyncio.Queue` through `call_soon_threadsafe`; only the UI thread touches
    widgets.
  - `StreamEvent.message` is set only on the closing event — that is how
    `_stream_round` tells progress from final.
  - Endpoints that ignore `stream=true` are handled by content-type sniffing
    (parsed as one plain completion), so the default is safe on old servers.
  - **Stopping** (`ctrl+x` / `/stop`, `self._stop_event`): mid-stream stops are
    clean (polled per chunk, connection closed, partial kept, flagged `stopped`,
    saved as the assistant turn with ` …[stopped]`). A stream that has gone
    *quiet* cannot be interrupted portably — a blocked socket read survives
    `resp.close()` from another thread — so `_stream_round` gives up after a
    0.25 s queue timeout and frees the UI; the abandoned request may keep running
    server-side. Stated to users in docs/configuration.md.
  - The streamed line enters `ui_lines` only at round end, with the final
    stripped text, so the log never holds half a reply.
  - `/compact`'s summary call stays non-streaming.
- **Smart autoscroll**: `_append` consults `_at_bottom()`; the view follows
  output only when you were already at the bottom, so scrolling up to reread
  sticks. Don't restore the unconditional `scroll_end`.

## Testing limitations / unverified

- The bug fixes above were exercised against a **local stub OpenAI-compatible
  server** driven through the real Textual TUI (Pilot), not a live vLLM/llama.cpp
  endpoint. Re-verify against a real endpoint before trusting:
  tool-calling round trips (knowledge tools) and `reasoning_effort` handling on
  models that actually think.
- `rolling`/`compact` were checked with the unit/TPI harness (fake
  `chat_completion`), not with a model whose template rejects mid-chat system
  messages. The fix (single leading system message) is by construction, not
  observed on a real template.
- Markdown/markup fix is verified for rendering; **no markdown styling** is
  applied (replies are intentionally plain text).
- **SSE tests need a chunked HTTP/1.1 fake server** (`SSEServer`,
  `tests/test_client.py`). Two traps, both hit for real: `http.server` buffers
  writes so nothing looks incremental, and an HTTP/1.0 body with no
  `Content-Length` makes urllib3 read the entire response before yielding one
  line (which made `iter_lines()` look broken when it was not). Real endpoints
  (vLLM, llama.cpp, Ollama) send `Transfer-Encoding: chunked`.
- Streaming was verified against that fake SSE server and the real TUI, not
  against vLLM/llama.cpp/Ollama. Check `prompt_tokens` usage and
  `reasoning_content` deltas on a real thinking model.

## Ops quick facts

- Dev endpoint URL/model and local overrides go in `.sekka/config.json`
  (auto-discovered, gitignored). See `LOCAL.md`.
- Tests: `python3 -m pytest tests/ -q` (Textual Pilot; ~130 tests, all should
  pass in ~55 s - the SSE tests sleep deliberately). `conftest.py` keeps tests off the real config/network.
- Tests must not name real internal services: one captive-portal test used to
  embed a real dashboard name; use generic stand-ins.
- A working tree once mysteriously lost files; recovery was via
  `git commit-tree` against known-good trees. Backup routine: see `LOCAL.md`.
