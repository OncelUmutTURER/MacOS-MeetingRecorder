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

If your assistant can open files on your Mac (Claude Code, Claude in Cowork, or any agent
with filesystem access), hand it the job instead of pasting. Two pieces: a **skill** that
teaches it how to write a report, and a **scheduled task** that runs daily so you never
think about it.

### 2a. The skill

Save this as a skill (in Claude Cowork: ask it to save a skill named `meeting-actions`;
in Claude Code: `.claude/skills/meeting-actions/SKILL.md`). Replace the two paths and the
`me.label` name with yours.

````markdown
---
name: "meeting-actions"
description: "Turn recorded meeting transcripts in ~/Meetings into English action-item
  reports. Use when the user asks to process a meeting, generate action items or
  summarise a meeting."
---

# Meeting Actions

Each meeting is a folder `~/Meetings/YYYY-MM-DD-HHMM[-title]/`. The leading
`YYYY-MM-DD-HHMM` is the meeting's **stamp**, and output files carry it in their name.

## Hard rules

1. **The report is always in <REPORT LANGUAGE>**, whatever language the meeting was in.
2. **Keep technical terms, product, company and site names exactly as spoken.** Never
   translate them.
3. **Never invent anything.** Owner or date not stated → `not stated`. Unsure whether
   something is a commitment → Open Questions, not Action Items.
4. **Never overwrite an existing report.** If `<stamp>-action-items.md` (or a legacy
   bare `action-items.md`) exists, that meeting is done.
5. **Write to `<stamp>-action-items.md`.** The pipeline detects processed meetings by
   that name. If your write tool alters the filename, rename it back — otherwise the
   meeting is listed as pending forever.
6. **Skip folders containing a `.skip` file.** That marker is deliberate.
7. `*-action-items.superseded.md` is **not** a report. A folder holding only that is
   still pending: it is the old report of a meeting that was later split in two.

## Steps

1. **Pick what to process.** If the user named a folder, do exactly that one. If they
   said "last meeting", take the most recent. Otherwise take pending meetings
   **newest first, at most 5**.
2. **Load the glossary** from `<PROJECT>/glossary.md` and use it to repair speech
   recognition damage silently. If you meet a new mishearing you are confident about,
   mention it in your reply so it can be added — do not edit the glossary yourself.
3. **Read the transcript.** Format is `**[HH:MM:SS] Speaker:** text`.
   - `<YOUR NAME>` is the user, from the microphone channel; that label is always right.
   - `SPEAKER_00 / SPEAKER_01 / …` are the others. If someone is addressed by name, map
     it: `SPEAKER_00 (likely Alex)`. If unsure, leave the label.
   - The header block lists channel levels, languages and warnings. If a warning
     suggests the transcript is incomplete, say so at the end of the report.
   - **Ignore prompt-echo lines** — whisper sometimes writes the jargon list into the
     transcript as if spoken. Nobody said that.
   - Transcripts sometimes end mid-sentence. Say so rather than guessing.
   - Meetings contain personal and off-topic talk. Leave it out and note roughly how
     much was skipped.
4. **Write the report** to `<folder>/<stamp>-action-items.md`:

   ```markdown
   # Action Items — <folder name>

   - **Meeting language:** <e.g. Turkish with English technical terms>
   - **Participants:** <YOUR NAME> · SPEAKER_00 (likely …) · SPEAKER_01
   - **Duration:** HH:MM:SS

   ## Summary
   Three to six sentences on what the meeting was about and where it landed.

   ## Decisions
   - <decision> — [HH:MM:SS]

   ## Action Items
   | # | Action | Owner | Due | Source |
   |---|---|---|---|---|
   | 1 | <what will be done> | <owner> | 2026-08-22 | [00:14:03] |

   ## Open Questions
   - <unresolved point> — [HH:MM:SS]

   ## My Items
   - [ ] <checklist of just the items the user owns>
   ```

   - Every decision and action item carries a `[HH:MM:SS]` timestamp.
   - Capture items owned by **anyone**, not just the user.
   - No decisions → `No firm decisions were reached in this meeting.`
     No action items → replace the table with `No action items identified.`
   - Convert relative dates to absolute where the meeting date makes it unambiguous,
     keeping the original phrasing in parentheses.
   - If two people claim the same task at different timestamps, flag it under Open
     Questions rather than silently picking one.
   - A transcript with fewer than ~40 speech lines is probably a false auto-record
     trigger: write a short report saying so instead of inventing content.
5. **Report back** in chat: one line per meeting — folder, number of action items, short
   gist — then how many are still pending. Do not paste report contents into the reply.
````

### 2b. The daily scheduled task

Ask your assistant to create a scheduled task (Claude Cowork has one built in; `cron`
plus `claude -p` works too) with cron `0 8 * * *` and this prompt:

> Generate action-item reports for up to FIVE recorded meetings that are still waiting
> for one, using the `meeting-actions` skill. Work entirely from local files.
>
> A folder in `~/Meetings/` is PENDING when it has `<stamp>-transcript.md`, has **no**
> `<stamp>-action-items.md` (a `*-action-items.superseded.md` does not count) and has no
> `.skip` file. Sort pending folders by name and process the **five NEWEST**.
>
> Afterwards append ONE block to `~/Meetings/.action-items-log.md` (create it if
> missing), even when nothing was pending:
>
> ```
> ## <YYYY-MM-DD HH:MM>
> - processed (<n>): <folder> · <folder> · …
> - skipped: <folder — reason>, … (or "none")
> - still pending (<n>): <folder> · <folder> · …
> ```
>
> Never rewrite earlier entries; only append.

Three things learned running this daily on a backlog of 80 meetings:

**Process the newest first, not the oldest.** Obvious in hindsight: you want yesterday's
notes today. It also matters mechanically — a meeting split with `meet split` keeps the
*original* timestamp, so every split jumps to the front of an oldest-first queue and
pushes today's meetings back. That looked exactly like the job repeating itself, and
cost a day of investigation.

**Five per run.** Transcripts are large. 80 of them came to 3.5 MB and 41,000 lines —
far beyond what fits in one session.

**Write a log line every run, including the empty ones.** Automation that leaves no trace
cannot be debugged. When the ordering problem above appeared, nothing on disk could say
what had happened; the log is what made the diagnosis possible.

### 2c. Keeping the two in sync

The skill and the scheduled task repeat the report template on purpose — the task runs
without a human present and must not depend on a skill being loaded. If you change the
template, change both, or your reports stop matching each other.

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
