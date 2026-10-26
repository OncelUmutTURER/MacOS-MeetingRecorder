# Action items

`meet stop` gives you a transcript. Turning that into a summary with decisions and
action items is a separate step, on purpose: it is the one part that needs a language
model big enough to reason, and you probably already pay for one.

Three ways to do it, cheapest first.

---

## 1. Paste it into an assistant you already have (default)

No API key, no extra cost. Open the transcript, copy it, and use this prompt:

````text
You are an experienced meeting secretary. Below is a meeting transcript with speaker
labels and timestamps. It may be in one language or a mix of two, and it is automatic
speech recognition output, so it contains errors.

Rules:
- Write the entire report in English.
- Keep technical terms, product/company/site names and industry jargon EXACTLY as
  spoken; do not translate them.
- The label "Me" is the person who recorded the meeting. SPEAKER_00, SPEAKER_01 and so
  on are the other participants; if the transcript makes a real name recoverable
  (someone addresses them by name), add it in brackets: SPEAKER_01 (likely Ahmet).
- Invent nothing that is not in the transcript. Where unsure, write "unclear".
- If an action item has no owner or no date in the transcript, write "not stated".
- Expect ASR errors in proper nouns. Use the glossary below to interpret them, and
  repair them silently — do not annotate every fix.

Return exactly this markdown structure:

## Summary
(3-6 sentences)

## Decisions
- (decision) — evidence: [timestamp]
(if none: "No clear decision was taken in this meeting.")

## Action Items
| # | Task | Owner | Due | Evidence |
|---|---|---|---|---|
(if none, replace the rows with "No action items.")

## Open Questions
- (question left unanswered) — evidence: [timestamp]

<glossary>
...paste glossary.md here, or leave this out...
</glossary>

<transcript>
...paste the transcript here...
</transcript>
````

Save the result as `<stamp>-action-items.md` inside the meeting folder — for example
`~/Meetings/2026-08-20-1430-weekly/2026-08-20-1430-action-items.md`. That filename is
what makes the meeting count as "done":

```bash
meet pending          # meetings with a transcript but no report
```

### Why the glossary belongs in this prompt

Speech recognition mangles proper nouns, and the report step is the last chance to fix
the ones that are too ambiguous to correct automatically. There is **no length limit**
here — unlike whisper's prompt, where more than ~16 terms measurably *hurts*. Paste the
whole glossary.

### Why the file name matters

All three output files carry the meeting's timestamp in their name
(`2026-08-20-1430-transcript.md`, not `transcript.md`). The reason is practical: these
get emailed to colleagues, and a dozen attachments all called `transcript.md` are
useless. Tools read both the stamped and the old flat name, but write the stamped one.

---

## 2. Automate it with an assistant that can read files

If your assistant can open files on your Mac (Claude Code, Claude in Cowork, or any
agent with filesystem access), you can hand it the job instead of pasting:

> Process the meetings in `~/Meetings` that have a transcript but no
> `*-action-items.md`. Oldest first, at most 5 per run. For each one, read the
> transcript, use `glossary.md` to interpret mangled terms, and write the report into
> the same folder using the template in `docs/ACTION-ITEMS.md`. Append one line per run
> to `~/Meetings/.action-items-log.md`: what you processed, what you skipped, how many
> are still waiting.

Three things we learned running this daily on a backlog of 80 meetings:

**Process the newest first, not the oldest.** Obvious in hindsight: you want yesterday's
notes today. It also matters mechanically — a meeting split with `meet split` keeps the
*original* timestamp, so every split jumps to the front of an oldest-first queue and
pushes today's meetings back. That looked exactly like the job repeating itself.

**Five per run.** Transcripts are large. 80 of them came to 3.5 MB and 41,000 lines —
far beyond what fits in one session.

**Write a log line every run.** Automation that leaves no trace cannot be debugged. When
the ordering problem above appeared, nothing on disk could say what had happened; the
log is what made the diagnosis possible.

`.superseded.md` files do not count as reports. `meet split` renames the old report to
that when a recording is split, because it described two meetings as one, and both
halves then correctly show up as pending again.

---

## 3. The API path

If you want it fully hands-off and do not mind paying per meeting:

```yaml
actions:
  enabled: true
  model: "claude-sonnet-4-6"
  max_tokens: 8000
  report_language: "English"
```

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
meet actions ~/Meetings/2026-08-20-1430-weekly
```

The prompt lives in `src/actions.py` and is kept identical to the one above. If you
change one, change the other, or your reports will stop matching.

---

## What a good report looks like

The value is not the summary — it is the evidence column. Every action item points at a
timestamp, so when someone disputes what was agreed, you can open the transcript at
that second and read the sentence. A report without timestamps is just a story about a
meeting.

Two failure modes worth watching for:

- **Invented owners.** If nobody said who would do it, the report must say "not stated".
  A model that guesses an owner creates an obligation that nobody agreed to.
- **Jargon silently translated.** "reline" becoming "repair" changes the meaning for
  anyone who knows the domain. Hence the rule about keeping terms exactly as spoken.
