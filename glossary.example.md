# Meeting Glossary — example

Copy this file to `glossary.md` and replace the examples with your own vocabulary:

```bash
cp glossary.example.md glossary.md
```

The glossary is **optional**. Without it the pipeline still works; you only lose the
term-correction step. `glossary.md` is in `.gitignore`, because yours will contain
product, customer and colleague names.

Two different things use this file, and confusing them is the most common mistake:

| | what it is | length limit |
|---|---|---|
| `transcribe.jargon_sets` in `config.yaml` | words put into whisper's `initial_prompt` **while decoding audio** | **16 terms** (measured) |
| this file | text applied **after** the transcript is written, plus context for the report step | **none** |

Adding terms here does **not** affect ASR accuracy and cannot slow anything down.
Adding terms to the prompt beyond ~16 makes accuracy *worse*. See ENGINEERING-NOTES.md.

---

## How to write a useful glossary

1. **Scannable lists, not prose.** A term buried in a comma-separated paragraph will
   not be found when the report step looks for it. One row per term.
2. **Measure before you add a correction.** Search your existing transcripts for the
   term first. Every time we did this, a term turned out to be mangled in 4–6
   different ways, not one.
3. **Only explicit mappings.** Never auto-correct by similarity — see the
   "Do NOT auto-correct" table below for why.

---

## Products & platforms

| Term | Meaning |
|---|---|
| **Acme Core** | the main platform (umbrella product) |
| **Widget-X** | dual-sensor camera unit (one word, capital X) |
| **BlueFalcon** | the variant of Widget-X sold to the glass industry |

## Acronyms

| Acronym | Expansion |
|---|---|
| **SLA** | Service Level Agreement |
| **PLC** | Programmable Logic Controller |
| **SCADA** | Supervisory Control and Data Acquisition |

## Customers, partners, sites, people

| Name | Note |
|---|---|
| **Northwind Steel** | customer, two plants |
| **Jane Okafor** | Northwind's maintenance lead |

## Domain vocabulary

| Term | Meaning |
|---|---|
| **ladle** | vessel that carries molten steel |
| **refractory** | the heat-resistant lining inside it |
| **campaign** | the service life of a lining between relines |

---

## Measured corruption rates

Keep a record of what you actually measured, so the next person does not re-discover
it. Numbers below are illustrative:

| Term | correct | mangled | error rate |
|---|---|---|---|
| BlueFalcon | 0 | "Blue Falcon" 22 · "BlueFalken" 38 | **100%** |
| SCADA | 44 | "scuda" 13 · "skater" 10 | 34% |

Terms that come out clean need no rule — do not add one "just in case".

---

## Do NOT auto-correct these — measured false positives

Before adding a fuzzy-looking rule, check the word in context. Every entry below was
checked in real transcripts and turned out to be a **real word**, not a mangled term:

| Looks like | Actually |
|---|---|
| "insight" | the ordinary word, not the product name Insite |
| "pilot" | the ordinary word, not Copilot |
| "case", "master", "cluster" | ordinary words, not "caster" |

A fuzzy matcher over a 200-term glossary would destroy every one of these lines.

---

## Likely ASR mishearings

**This is the only table the code reads.** `src/fixterms.py` applies it to the
transcript after it is written; the raw text is kept as `<stamp>-transcript.raw.md`
and every change is logged in `<stamp>-corrections.md`, so `meet fixterms <folder>
--undo` puts it back. Re-running re-applies from the raw file, so fixing a bad rule
repairs old damage automatically.

Format: left column = variants **in double quotes**, comma-separated. Right column =
the correct form **in bold**.

| Heard as | Almost certainly |
|---|---|
| "Blue Falcon", "BlueFalken", "blue falken" | **BlueFalcon** |
| "Widget Ex", "widget ex" | **Widget-X** |
| "scuda", "skater", "skadda" | **SCADA** |
| "north wind", "Northwynd" | **Northwind** |

Four rules, each learned from a rule that caused real damage:

1. **Ambiguous replacements are skipped.** If the right column contains `/` or
   parentheses (`pulpit crew / pulpit team`), the tool skips the row rather than
   writing a slash into your transcript.
2. **Do not swallow grammatical endings.** In an inflected language, write the
   inflected variant as its own row with the ending preserved on both sides:
   `"ladlenin" → ladle'nin`, not `"ladlenin" → ladle`.
3. **Never add a variant you have not verified.** A rule mapping `"kes"` to `k3s`
   destroyed the only occurrence of an ordinary word spelled the same way.
4. **The delimiter is the double quote only.** An apostrophe is part of a word in
   many languages; treating it as a delimiter silently truncated rules and produced
   doubled endings in the output.

`tests/test_fixterms.py` tests both the terms that must be fixed and the words that
must be **left alone**. The second group matters more: a false positive deletes real
speech. Run `meet test` after editing this table.
