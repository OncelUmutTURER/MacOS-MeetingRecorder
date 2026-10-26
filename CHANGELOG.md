# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/).

For this project a version number means:

- **MAJOR** — you have to do something by hand after `git pull` (a config key was
  renamed, a device must be reconfigured, recordings need migrating).
- **MINOR** — new capability; `git pull` is enough, old recordings keep working.
- **PATCH** — fixes only.

Entries that come from a measurement say so, and the number is the point. If a
default changed, the entry tells you what was measured.

## [Unreleased]

Nothing yet.

## [1.0.0] — 2026-10-26

First public release. Everything below already ran daily for months on bilingual
engineering meetings before being published.

### Recording

- Two-channel capture: microphone and system audio (BlackHole loopback) as two
  independent ffmpeg processes, so "me" and "everyone else" come free from the
  channel split and a headset dying mid-call only rebuilds one of them.
- Microphone parts are aligned by the absolute epoch ffmpeg writes to its log, not
  by assumption. Measured error: 0.5 s at a device change, 12 ms between channels
  at start.
- Guard process: keeps the system output on the right Multi-Output device, moves
  the microphone channel when a better device appears, and warns when a channel
  has been silent too long.
- Loopback self-test at the start of every recording — BlackHole reads exactly
  −180 dBFS when idle, so "nobody is talking" and "nothing is reaching us" cannot
  be told apart by level; a short quiet tone can.
- Automatic recording: starts when the microphone has been in use for 15 s and a
  meeting app is running, stops when the output device goes idle for 25 s.

### Transcription

- whisper large-v3 via MLX, VAD-gated. Silence is never handed to the model,
  which used to produce hallucination loops and 20-second timestamp shifts.
- Language decided per speech window, never locked — locking makes whisper
  translate instead of transcribe. Windows under 1.5 s are pulled to the meeting's
  dominant language, derived from the microphone channel's long windows (measured
  over 86 meetings: mislabelled windows had a median duration of 0.7 s, 2 words,
  and 39% were filler sounds).
- Prompt-echo filter: whisper wrote the jargon prompt into the transcript as if
  spoken, three times in one 97-minute recording.
- Jargon prompt capped at 16 terms. Measured on the same audio with 10 target
  terms: no prompt 0/10, 10 terms 9/10, 27 terms 6/10, 40 terms 6/10.
- Speaker diarization with `pyannote/speaker-diarization-community-1` (free,
  local, MPS with CPU fallback).
- Glossary-driven term correction, applied after the transcript is written and
  fully reversible. Only explicit mappings — a fuzzy matcher was measured against
  82 transcripts and would have destroyed hundreds of ordinary words.

### Timeline correction

- The capture is behind the wall clock and the two channels drift apart at
  different rates: measured across 119 recordings, `sys.wav` is a median 6.4%
  short, up to 13.8%, and a 90-second controlled test put the two channels 9.7 s
  apart after 105 seconds.
- Fixed without touching the audio: a file-time ↔ wall-clock map is built from
  ffmpeg's own progress output and applied to the transcript timestamps. The
  alternative (`aresample` compensation) fixes the clock but injects ~103 ms of
  silence into speech, which measurably costs 2% of the words.

### Tools

- `meet split` — two meetings recorded into one file, separated at the transcript
  level with no re-transcription, audio cut losslessly, metadata reconciled.
  Re-splitting a part extends the series instead of starting a new one.
- `meet trim` — cut a tail or a head, audio and transcript together.
- `meet inspect` — levels, RMS profile over time, guard log, dropout counts,
  channel drift, the device formats ffmpeg actually opened.
- `meet audit [--fix]` — ghost processes, leftovers, stale state.
- `meet check` — 14 checks covering the whole installation.
- `meet snooze <min>` — pause automatic recording for a while; it re-arms itself.
- `meet test` — 10 regression test files.

### Safety and hygiene

- ffmpeg processes are escalated to SIGKILL and verified dead. Before this, 10
  orphaned processes were found running, the oldest 9 days old, all holding a
  microphone input.
- The guard can no longer resurrect a stopped recording — a race that once spawned
  six transcription processes for the same meeting.
- Flapping protection: one meeting produced 67 folders in 73 minutes because the
  stop signal was wrong. Worst case is now 3.
- Recording stops when the microphone device disappears (the headset went into its
  case). 28 of 168 recordings used to never stop on their own.
- The output device is never left stuck on a Multi-Output.

### Documentation

- `README.md`, `docs/SETUP.md`, `docs/GLOSSARY.md`, `docs/ACTION-ITEMS.md`.
- `ENGINEERING-NOTES.md` — the measurements behind every non-obvious default,
  including the hypotheses that were eliminated, so they are not retried.

### Added in this release for publication

- Update check (`meet version`, and a menu bar item). It reports a newer release
  and hands you the `git pull` command; it never downloads or installs anything.
  Off with `updates.enabled: false`.
- `config.example.yaml` and `glossary.example.md`; the real ones stay local.
- MIT licence.
