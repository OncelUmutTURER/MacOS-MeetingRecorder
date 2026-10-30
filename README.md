# meeting-pipeline

Local meeting recorder, transcriber and note generator for macOS. Records **you and
the other side as two separate channels**, transcribes both on your own machine, works
out who said what, and gives you a transcript you can turn into action items.

Nothing is uploaded. No account, no API key, no subscription. The models run on your
Mac and the recordings stay in a folder you choose.

```
Teams / Zoom / Meet / a conversation in the room
        │
        ├── microphone  ──► mic.wav   ──┐
        │                               ├──► whisper ──► diarization ──► transcript.md
        └── BlackHole loopback ─► sys.wav ─┘                                   │
                                                                      action items
```

Built and used daily for bilingual (Turkish/English) engineering meetings, but nothing
in it is specific to those languages — whisper handles 99 and you choose which ones it
may pick from.

---

## Why two channels

Most note-takers record one mixed stream. This one keeps the microphone and the system
audio apart, and that single decision buys a lot:

- **"Me" vs "everyone else" is free** — it comes from the channel, not from voice
  fingerprinting. Speaker diarization then only has to separate the people on the far
  side.
- **The far side is captured at full quality** even when you are on speakers, because
  it comes from the audio device, not from a microphone pointed at a laptop.
- **When your headset dies mid-call** (Bluetooth HFP does this constantly) only the
  microphone channel is rebuilt; the far side is never interrupted.

The cost is complexity, and most of this repository is that complexity being handled:
device switching, ghost processes, clock drift, flapping auto-start. Every non-obvious
default has a comment explaining the measurement behind it.

---

## What you get per meeting

```
~/Meetings/2026-08-20-1430-weekly/
├── mic.wav / sys.wav                  the two raw channels
├── 2026-08-20-1430-transcript.md      merged, speaker-labelled, timestamped
├── 2026-08-20-1430-transcript.json    the same thing, machine-readable
└── 2026-08-20-1430-action-items.md    summary, decisions, action items, open questions
```

The transcript looks like this:

```
**[00:03:41] Me:** Can we move the reline to next week?
**[00:03:49] SPEAKER_01:** That depends on the scan — I'll have it on Friday.
```

---

## Requirements

- **Apple Silicon Mac.** The transcription runs on MLX, which is Apple-GPU only.
- macOS 13+, Python 3.12, ~6 GB free disk (3 GB of that is the model cache).
- [BlackHole 2ch](https://existential.audio/blackhole/) — free virtual audio driver.
- ffmpeg, SwitchAudioSource, terminal-notifier (all from Homebrew).
- A free [Hugging Face](https://huggingface.co) account, to download the models.

Everything above is free. There is no paid tier of anything in this project.

---

## Install

```bash
git clone https://github.com/OncelUmutTURER/MacOS-MeetingRecorder.git meeting-pipeline
cd meeting-pipeline

brew install blackhole-2ch ffmpeg switchaudio-osx terminal-notifier
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt

cp config.example.yaml config.yaml      # then edit it — see docs/SETUP.md
cp glossary.example.md glossary.md      # optional, but worth it
```

Then follow **[docs/SETUP.md](docs/SETUP.md)**, which covers the parts that cannot be
scripted: creating the Multi-Output devices in Audio MIDI Setup, getting a Hugging Face
token, accepting the model licences, and granting microphone permission.

Verify the whole installation in one command:

```bash
./bin/meet check
```

It runs 14 checks — tools on PATH, BlackHole, heavy dependencies, CoreAudio access,
your Multi-Output devices, microphone selection, token permissions, the meetings
folder, ghost processes, the launch agent. Do not skip it; most "it does not work"
reports are one red line in there.

Optionally put `meet` on your PATH and start the menu bar app at login:

```bash
ln -s "$PWD/bin/meet" ~/.local/bin/meet
meet install-agent
```

---

## Daily use

The menu bar app is the real interface. **Its labels and notifications are currently in
Turkish**; the CLI, the configuration, the documentation and every generated report are
in English. Localising the UI is an open issue and a good first pull request.

| Menu item | What it does |
|---|---|
| **Kaydı Başlat / Durdur** (start / stop recording) | manual control; the title shows elapsed time |
| **Toplantı Dili** (meeting language) | pin a language mid-meeting without cutting the recording |
| **Toplantıda Otomatik Kaydet** (record automatically) | start when a call starts, stop when it ends |
| **Otomatik Kaydı Duraklat** (pause auto-recording) | 15 / 30 / 60 min — also stops the current recording, then re-arms itself |
| **Mikrofon Kontrolü** (microphone check) | 10-second diagnostic recording |
| **Transkript Çıkar** (transcribe) | lists every meeting that has audio but no transcript, newest first — pick one |
| **Action Item Raporu** | prepare the report command for the meetings still waiting |
| **Meetings Klasörünü Aç** (open meetings folder) | opens the recordings folder |
| **Sürüm 1.0.0** (version) | current version; says when a newer release exists |

Or from the terminal:

```bash
meet start ["title"]      # start recording
meet stop                 # stop; transcription starts in the background
meet status               # elapsed time, microphone, part, output device
meet snooze 30            # do not record anything for 30 minutes
meet lang tr              # mark the language from here on
meet pending              # meetings with no action-item report yet
meet inspect [folder]     # levels, RMS profile over time, guard log, device formats
meet split [folder] --at 01:12:30    # two meetings landed in one recording
meet trim  [folder] --to 01:05:00    # cut a tail the recorder kept going into
meet audit [--fix]        # ghost processes, leftovers, stale state
meet version              # version + new-release check (never installs anything)
meet test                 # the regression suite (10 files)
```

`meet help` lists everything.

---

## Transcription quality

Three things do most of the work, and each one is there because of a measured failure:

**Voice activity detection is mandatory.** In a two-channel recording each channel is
silent more than half the time, and whisper hallucinates loops in long digital silence
(in one test it repeated the same phrase thirty times and shifted timestamps by 20 s).
Silence is never handed to the model.

**Language is decided per window, never locked.** Locking a channel to one language
makes whisper *translate* foreign speech instead of transcribing it. Windows shorter
than 1.5 s are pulled to the meeting's dominant language, because at that length the
detector is a coin flip — measured over 86 meetings, mislabelled windows had a median
duration of 0.7 s and 39% of them were filler sounds.

**A short jargon prompt, a long glossary.** Putting your vocabulary into whisper's
prompt helps up to about 16 terms and *hurts* beyond that. Everything else goes into
`glossary.md`, which is applied to the text afterwards, where length costs nothing.
See [docs/GLOSSARY.md](docs/GLOSSARY.md).

There is more of this — clock drift between the two channels, ghost ffmpeg processes,
auto-start flapping — in **[ENGINEERING-NOTES.md](ENGINEERING-NOTES.md)**. If you plan
to change a default, read that first; several of them were wrong once and the note says
how we found out.

---

## Action items

`meet stop` gives you a transcript. Turning it into a report is a separate step, and by
default it does not use an API key: you paste the transcript into whatever assistant you
already pay for, with the prompt in **[docs/ACTION-ITEMS.md](docs/ACTION-ITEMS.md)**.
That document also covers running it automatically for a backlog of meetings, and the
optional API path if you would rather it be hands-off.

---

## Updates

```bash
meet version          # what you have, and whether there is a newer release
```

The menu bar shows the version at the bottom and turns into **▸ Yeni Sürüm Var** when a
newer release exists; clicking it opens the release notes and copies the update command.

**It never downloads or installs anything.** Installation is `git clone`, so updating is:

```bash
cd meeting-pipeline && git pull && ./venv/bin/pip install -q -r requirements.txt && ./bin/meet check
```

That is deliberate. A recording tool that rewrites its own code in the background can do
it in the middle of a meeting; this one only tells you, and you (or an agent you trust)
run the command. `meet check` at the end is what tells you the update landed cleanly.

The check asks GitHub's public API for the latest release, at most once a day, and
caches the answer. Turn it off with `updates.enabled: false` and no request is made.

What changed in each version is in [CHANGELOG.md](CHANGELOG.md).

## Privacy

Audio, transcripts and reports stay on your machine. The project makes network requests
for exactly three things, and nothing else:

1. **Downloading the models**, the first time.
2. **The update check** — a plain GET to GitHub's public API, once a day, carrying no
   meeting data and no identifier. `updates.enabled: false` disables it entirely.
3. **Whatever you choose to do with the transcript afterwards** — if you paste it into a
   cloud assistant to get action items, that is your decision and your data leaving.

**Recording other people has legal and ethical requirements that vary by country and by
company.** In many places you must tell participants they are being recorded, and in
some you need their consent. This tool makes recording easy; it does not make it legal.
That part is on you.

---

## Project layout

```
bin/meet            the CLI entry point; every command is a subcommand of it
src/record.py       capture, device management, the guard process
src/audiowatch.py   CoreAudio device monitoring, automatic start/stop
src/transcribe.py   VAD, windowing, language detection, whisper
src/diarize.py      pyannote, with MPS → CPU fallback
src/timeline.py     file-time ↔ wall-clock correction (see ENGINEERING-NOTES.md)
src/pipeline.py     ties it together, writes the transcript
src/fixterms.py     applies the glossary's correction table
src/menubar.py      the menu bar app
src/update.py       new-version check (notifies only — never downloads)
tests/              regression tests — `meet test`
docs/               setup, glossary and action-item guides
```

---

## Releasing (for maintainers)

The update check reads GitHub's latest **release**, so a tag alone is not enough:

```bash
# 1. bump VERSION and add the entry to CHANGELOG.md, then commit
# 2. tag and publish
git tag -a v1.1.0 -m "v1.1.0" && git push origin v1.1.0
gh release create v1.1.0 --title "v1.1.0" --notes-file <(sed -n '/## \[1.1.0\]/,/^## /p' CHANGELOG.md)
```

Everyone's menu bar picks it up within a day. If no release exists yet, the check falls
back to the newest tag; if there is neither, it stays quiet.

## Contributing

Issues and pull requests are welcome. One house rule, learned the hard way: **measure
before you claim.** If you change a default, include the measurement that justifies it,
and add a test for the behaviour that must *not* change. In this project the tests that
check what should be left alone have caught more real bugs than the ones that check
what should happen.

## Licence

MIT — see [LICENSE](LICENSE). The models have their own terms: whisper (MIT),
pyannote speaker-diarization-community-1 (CC-BY-4.0, free, local), BlackHole (GPL-3,
installed separately as its own program).
