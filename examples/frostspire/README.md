# The Frostspire Marches

A campaign pack for sekka: a game-master prompt, an opening scene, a character for
you to play, and four lore files the model looks up as tools instead of carrying in
the system prompt. Everything here is invented.

```
frostspire/
  .sekka/campaign.json     GM prompt, labels, greeting, your character, the lore list
  .sekka/config.json       optional and minimal: endpoint placeholder + context mode
  knowledge/world.md       geography, factions, magic rules, tone and boundaries
  knowledge/seraine.md     character card: the frost-mage at the Last Hearth
  knowledge/pock.md        character card: the toll-man's boy
  knowledge/caer-dhul.md   location brief: the hill town above the locks
  README.md                this file
```

## Playing it

Install it once, then play it from anywhere:

```bash
sekka pack install examples/frostspire
sekka --pack frostspire --endpoint http://your-host:8000/v1 --model your-model-id
```

`--pack` is what makes the lore files findable. Lore paths inside a pack are relative
to the pack root, so an installed pack works no matter where you were standing. If you
skip `--pack` and point sekka at the campaign file directly, or play from this folder,
that also works - just don't run it from somewhere else, because then the four lore
files are silently not there and the GM will invent a cast. `/knowledge` is how you
check which cards sekka actually loaded.

Two other ways in, if you prefer not to install:

```bash
sekka --pack examples/frostspire            # straight out of a checkout, no install
cd examples/frostspire && sekka             # uses .sekka/config.json beside it
```

The endpoint can come from the environment instead, which outranks the campaign:
`SEKKA_ENDPOINT`, `SEKKA_MODEL`, `SEKKA_API_KEY`.

## What is in the campaign file

- `system_prompt` - the GM's instructions: voice NPCs from their cards only, track
  inventory and promises, keep scenes to a few paragraphs, never act for the player.
- `labels` - the transcript is voiced `Player:` / `GM:` instead of `You:` / `Assistant:`.
- `greeting` - the opening scene, written by the pack rather than generated.
- `player` - who you are. Edit this to play someone else; nothing else refers to it.
- `knowledge` - one entry per lore file, each with a `description` (the only pitch the
  model hears before deciding to open the file) and `keywords`. Keywords make it
  automatic: mention Seraine and her card is in context before your message is sent,
  with no tool call and no round trip.
- `note` - a pinned scratchpad, empty as shipped. `/note` fills it and writes it back
  into the campaign file, which is why packs are read-only (below).
- `reasoning: low`, `temperature: 0.9`.

## Play state, and why packs are read-only

An installed pack is read-only: `/note` and the campaign half of `/save` are refused,
because the pack on disk is shared by every session that plays it and a note is one
player's memory. Keep a copy you own:

```bash
sekka pack fork frostspire ~/play/marches     # then: sekka --pack ~/play/marches
```

A fork is a plain directory, so you can edit the prompt, add lore files, rewrite the
greeting, and keep notes in it. That is the intended way to make this campaign yours.

## Tweaking it

- Different genre, same machinery: rewrite `system_prompt`, swap the lore files, keep
  the shape. The `description` fields are the part worth stealing - they are written as
  "what is in here and when to reach for it", which is exactly what a model needs to
  decide whether to open the file.
- `enabled: false` on a lore entry keeps the tool defined but off until you want it.
- `always: true` on an entry injects it at startup instead of waiting for a tool call -
  fine for one short file, wasteful for four.
- Tune `context_window`, `context_mode` and `reasoning` in `/config` for your endpoint.
  The shipped values are illustrative.

Lore files are capped at 64 KB and read as `utf-8`; keep them markdown or plain text.
A pack cannot reference anything outside its own directory, which is enforced at
install time rather than trusted.
