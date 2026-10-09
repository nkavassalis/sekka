# sekka examples

Two example setups to seed your imagination. Both are ordinary directories - copy
one, install it, or play it in place.

| Example | What it shows off |
|---------|-------------------|
| [`frostspire/`](frostspire/) | A full **campaign pack**: GM prompt, opening scene, your character, and four lore files (world, two character cards, one location) exposed as tools, so the model "knows" its cast without cramming it into the system prompt. Has its own [setup notes](frostspire/README.md) |
| [`medical-practice/`](medical-practice/) | Workflow knowledge bases (check-in, insurance, payments, refills, triage) for a hypothetical clinic assistant |

## The campaign pack

```bash
sekka pack install examples/frostspire                 # copies it into ~/.sekka/packs
sekka --pack frostspire --endpoint http://your-host:8000/v1
```

`sekka pack list` shows what is installed, `sekka pack show NAME` prints what a pack
holds, `sekka pack fork NAME DIR` copies one somewhere you can edit. A pack is
read-only while installed, so keep a fork before you start rewriting the prompt or
taking notes. See [frostspire/README.md](frostspire/README.md) and
[docs/configuration.md](../docs/configuration.md#packs).

Playing it without installing also works:

```bash
sekka --pack examples/frostspire          # straight out of the checkout
cd examples/frostspire && sekka           # via the .sekka/config.json beside it
```

**If you skip `--pack`, run it from inside the folder.** Lore paths in a campaign file
are relative to the campaign's directory, and without a pack root sekka falls back to
the current working directory - so launched from elsewhere, the greeting and system
prompt arrive and the four lore files quietly do not. `/knowledge` tells you which
files actually loaded. `--pack` is what fixes this, which is the main reason it exists.

## The knowledge-base example

`medical-practice/` is not a campaign: it is a plain chat client pointed at five
workflow documents, configured entirely through `.sekka/config.json`.

```bash
cp -r examples/medical-practice ~/sekka-clinic && cd ~/sekka-clinic
sekka --endpoint http://your-host:8000/v1
```

## Things to know

- **All content is fictional.** Names, places, and procedures are invented. The
  medical example is a *hypothetical* practice with *made-up* policies - it is not
  medical, billing, or legal advice, and the model will happily hallucinate
  confidently if you treat these docs as anything but examples.
- **Knowledge files are readable by the model** (only the ones you enable, and only
  via their fixed tools - sekka never lets the model name a file). Don't put real
  secrets in them.
- **Character cards trigger on keywords** (the `keywords` field on each entry):
  mention Seraine and her card is in context before the model ever sees your message,
  with no tool call needed. The `description` still matters for entries without
  keywords, which the model must look up itself.
- The `description` fields are deliberately written as "what's in here and when to
  reach for it" - that text is the only pitch the model hears before deciding to call
  the tool. Steal that style.
- **`note` ships empty** in both examples. It is play state, not part of the
  scenario: `/note` writes into the campaign file, so a shipped note would open every
  stranger's game mid-sentence.
- Tune `context_window`/`context_mode`/`reasoning` for your own endpoint in
  `/config`; the shipped values are illustrative.
