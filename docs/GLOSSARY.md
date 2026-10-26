# Vocabulary: the jargon prompt and the glossary

Speech recognition is good at ordinary language and bad at your product names. This
project attacks that in two places, and the single most common mistake is confusing
them.

| | `transcribe.jargon_sets` (config.yaml) | `glossary.md` |
|---|---|---|
| Fed to | whisper's `initial_prompt`, **while decoding audio** | the text, **after** the transcript is written |
| Length limit | **16 terms** — measured, hard | **none** |
| Effect of adding more | accuracy gets **worse** | nothing, except more corrections |
| Risk | prompt terms leak into the transcript | a bad rule corrupts real speech |

Adding a term to `glossary.md` cannot affect ASR accuracy. Adding a 40th term to the
prompt measurably damages it.

---

## The prompt: keep it short

Measured on the same audio with 10 target terms:

| prompt | correctly transcribed |
|---|---|
| no prompt | 0 / 10 |
| 10 terms | **9 / 10** |
| 27 terms | 6 / 10 |
| 40 terms | 6 / 10 |

Worse, a long list makes whisper *write the list into the transcript* as if it had been
spoken. On a real 97-minute recording it did this three times, in low-signal windows.
There is a filter for it (`_is_prompt_echo` in `src/transcribe.py`, tested in
`tests/test_prompt_echo.py`), but prevention is better: `max_prompt_terms: 16`.

Pick the sixteen words your meetings cannot do without — product names, acronyms,
customer names — and put everything else in the glossary. Use `jargon_profile` to
switch sets if you work across domains:

```yaml
transcribe:
  jargon_profile: "steel"     # core | steel | glass | both
  max_prompt_terms: 16
  jargon_sets:
    core: ["roadmap", "backlog", "KPI"]
    steel: ["ladle", "refractory", "tundish", "slagline"]
```

---

## The glossary: the only automatic corrections

`src/fixterms.py` reads exactly one table from `glossary.md` — the one under
`## Likely ASR mishearings` — and applies it after the transcript is written. The rest
of the file is for humans and for the report step.

```markdown
| Heard as | Almost certainly |
|---|---|
| "Blue Falcon", "BlueFalken", "blue falken" | **BlueFalcon** |
```

Left column: variants in **double quotes**, comma-separated. Right column: the correct
form in **bold**.

Everything is reversible. The untouched text is kept as `<stamp>-transcript.raw.md`,
every change is listed in `<stamp>-corrections.md`, and:

```bash
meet fixterms <folder> --dry-run   # show what would change
meet fixterms <folder>             # apply
meet fixterms <folder> --undo      # restore
meet fixterms --all                # re-apply across every meeting
```

Re-running always starts from the raw file, so when you fix a bad rule the old damage
is undone automatically.

---

## Never correct by similarity

This is the rule the whole design rests on. We checked fuzzy matches against 82 real
transcripts, in context, one by one. Every one of these was a **real word** being used
normally, not a mangled product name:

| Fuzzy match would "fix" | occurrences | what it actually was |
|---|---|---|
| "insight" → Insite | 80 | the ordinary word ("the warning overrides the insight") |
| "pilot" → Copilot | 38 | the ordinary word |
| "face" → FCE | 11 | "Hugging Face", "North Face" |
| "of" → BOF | 1015 | the preposition |
| "case", "master", "cluster" → caster | many | ordinary words |

A similarity matcher over a 200-term glossary would have destroyed every one of those
lines. Only explicit, verified mappings are applied.

---

## How to add a term properly

The method, learned by doing it wrong first:

1. **Do not trust the single spelling you remember.** Search your existing transcripts
   for the term. Every time we did this, it came back mangled in 4–6 different ways.
   One term had a 100% error rate — not a single correct occurrence in 81 tries.
2. **Verify each variant in context.** Read the surrounding sentence. If the "mangled"
   form is also a real word in any language your meetings use, do not add the rule.
3. **Add look-alikes to the do-not-touch test**, not just the fixes.
4. **Dry-run, then apply retroactively**: `meet fixterms --all`.

Four rules exist because a rule caused real damage:

- **Ambiguous right-hand sides are skipped.** A `/` or parentheses in the correct column
  would write a slash into your transcript. 16 rules in our own glossary are skipped for
  this reason; they are left for the report step to interpret.
- **Do not swallow grammatical endings.** In an inflected language, `"ladlenin" → ladle`
  deletes the suffix. Write `"ladlenin" → ladle'nin` as its own row.
- **Never add a variant you have not verified.** A rule mapping `"kes"` to `k3s`
  destroyed the single occurrence of an ordinary word spelled identically.
- **The apostrophe is not a quote delimiter.** Treating it as one truncated rules and
  produced doubled endings in the output. Only `"` counts.

---

## Does it work?

On our own corpus: **239 corrections across 46 meetings**, with zero false positives
after the rules above were in place. Measured error rates before correction ran from
34% to 100% depending on the term — the worst offenders being short product names that
sound like ordinary words in the other language being spoken.

`tests/test_fixterms.py` covers both directions: terms that must be fixed, and words
that must be left alone. The second group is the one that matters. Run `meet test`
after every glossary edit.
