# AGENT.md — maintainer notes for sekka

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
    raise MountError.
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
- Endpoints that return **HTML instead of JSON** (captive portals, e.g.
  sparkDash on :8000) now raise a clear ClientError via content-type check.
- Context meter math: `used = max(exact API usage, char/4 estimate incl.
  system prompt)`; the send guard reserves ~10% of the window for the reply.
- Pause mode must not mutate `chat` (it rolls back the user turn on errors —
  same rollback pattern as API failures).

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
- Saves: timestamped files, confirm-before-write, no autosave by default.

## Deployment (sekka.org)

- GitHub Pages (repo `nkavassalis/sekka`, `main` branch, `/` root) behind
  CloudFront `E3N86H03FRP00P`; Route 53 zone `Z08634643RT85W4CD51EI` has
  apex+www A/AAAA aliases to the distribution.
- **CloudFront Function `sekka-host-override`** (viewer-request): rewrites
  origin Host to `sekka.org` (GitHub rejects the raw cloudfront-fetched
  hostname) and 301s www → apex. Don't remove it; GitHub Pages "custom
  domain" also lives in the CNAME file.
- ACM cert is in **us-east-1** (CloudFront requirement), DNS-validated.
- Default cache TTL 600 s — after pushing to `index.html`, run
  `aws cloudfront create-invalidation --distribution-id E3N86H03FRP00P --paths "/*"`.
- AWS profiles: default user `websites` (no IAM/Route53 list perms);
  Route53 write creds in `~/.aws/route53.ini` (`--profile`).

## Ops quick facts

- Dev/test endpoint: `http://10.1.13.99:8000/v1` (vLLM, model
  `qwen3.8-flash-next`, `max_model_len=262144`). Local dev config lives in
  `.sekka/config.json` in the repo dir (auto-discovered, gitignored).
- Tests: `python3 -m pytest tests/ -q` (Textual Pilot; ~75 tests, all should
  pass in ~15 s). `conftest.py` keeps tests off the real config/network.
- A working tree once mysteriously lost files; recovery was via
  `git commit-tree` against known-good trees. Re-do `sekka-backup` after
  big verified milestones.
