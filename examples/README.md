# sekka examples

Two example setups to seed your imagination. Copy one, `cd` into it, point
`--endpoint` at your server (or edit `.sekka/config.json`), and run `sekka`.
sekka auto-discovers `./.sekka/config.json`, and knowledge paths are relative
to the directory you run it from - so **run sekka from inside the example
folder**.

```bash
cp -r examples/roleplaying ~/sekka-rp && cd ~/sekka-rp
sekka --endpoint http://your-host:8000/v1
```

| Example | What it shows off |
|---------|-------------------|
| [`roleplaying/`](roleplaying/)      | A full **campaign**: `campaign.json` holds the GM prompt, labels, opening scene and your character; character cards and world lore are tools, so the model "knows" its cast without cramming it into the system prompt |
| [`medical-practice/`](medical-practice/) | Workflow knowledge bases (check-in, insurance, payments, refills, triage) for a hypothetical clinic assistant |

## Things to know

- **The roleplaying example is a campaign.** `.sekka/config.json` only points at
  an endpoint and picks a context mode; the fiction lives in
  `.sekka/campaign.json` beside it. Copy that pair to start your own scenario.
- **All content is fictional.** Names, places, and procedures are invented.
  The medical example is a *hypothetical* practice with *made-up* policies -
  it is not medical, billing, or legal advice, and the model will happily
  hallucinate confidently if you treat these docs as anything but examples.
- **Knowledge files are readable by the model** (only the ones you enable,
  and only via their fixed tools - sekka never lets the model name a file).
  Don't put real secrets in them.
- **Character cards trigger on keywords** (the `keywords` field in
  `campaign.json`): mention Seraine and her card is in context before the model
  ever sees your message, with no tool call needed. The `description` still
  matters for entries without keywords, which the model must look up itself.
- The `description` fields in each config are deliberately written as
  "what's in here and when to reach for it" - that text is the only pitch
  the model hears before deciding to call the tool. Steal that style.
- Tune `context_window`/`context_mode`/`reasoning` for your own endpoint in
  `/config`; the shipped values are illustrative.
