# Engineering notes

Everything in this file was measured on real recordings. It exists so the next person —
human or AI agent — does not rediscover the same things the hard way.

**If you are about to change a default, read the note first.** Several of these values
were wrong once, and the note says how we found out. Where a number is given, it came
from an experiment you can repeat; the scripts are in `bin/` and start with `_`.

House rule: **measure before you claim**. Twice in this project something was asserted
from reasoning alone and both times it was wrong.

---

## Architecture in one page

Two independent ffmpeg processes:

- **mic** → `mic.partNN.wav` — in parts, because the device can change mid-meeting
- **sys** → `sys.wav` — BlackHole loopback, never interrupted

They are separate processes for one reason: when a headset connects mid-meeting the
microphone channel has to move to it, and that must not interrupt the loopback.

Parts are aligned **by measurement, not by assumption**: ffmpeg writes the absolute
epoch of each input into its log (`start: 1787233202.706233`), `_stream_epoch()` reads
it, and `_assemble_mic()` places each part with `adelay` + `amix`, filling the gaps with
silence. Measured alignment error: 0.5 s gap at a device change, and 12 ms (median)
between the two channels at start.

A **guard** process runs for the length of each recording: it keeps the output device on
the right Multi-Output, rotates the microphone when a better one appears, warns on long
silence, and stops the recording when the headset disappears.

Diarization runs on the **loopback channel only**. The microphone channel is labelled as
you directly — "me vs. everyone else" comes free from the channel split.

---

## Capture is behind the wall clock, and the channels drift apart

The single most consequential finding in this project.

avfoundation does not deliver samples at the rate it reports. ffmpeg's progress line
contains both facts: `size=` is bytes written, `time=` is real elapsed time (because of
`-use_wallclock_as_timestamps`). They disagree:

| measurement | result |
|---|---|
| 119 recordings, `sys.wav` ÷ wall clock | median **6.4% short** |
| worst recording | 13.8% — ten minutes missing from an 88-minute meeting |
| slices within one meeting | ranges from 5% to 18% — **not constant** |
| 90-second controlled test | sys 5.0% · **mic 14.2%** → 9.7 s apart after 105 seconds |
| 140 untouched recordings, `mic.wav` − `sys.wav` | median 58 s, 105 of them over 5 s |

So minute 40 of `sys.wav` is really minute 43, and the microphone is further behind
still. Merge the two channels naively and, late in a long meeting, your sentences and
the other side's appear in the wrong order.

**Eliminated hypotheses — do not retry:** ffmpeg logs no `thread message queue` or
`dropping` warnings · forcing the input rate is impossible (`-ar` before `-i` returns
"Option not found"; avfoundation rejects it) · removing `-use_wallclock_as_timestamps`
does not help (2.87% / 7.85%).

### The fix: correct the timeline, not the audio

Controlled test (`bin/_synctest.sh` — 30 s silence then 30 s tone, fixed wall duration,
variants run **sequentially**):

| variant | sys | mic | channel gap in 60 s |
|---|---|---|---|
| current (no compensation) | −3.26% | −7.96% | **−2.82 s** |
| `aresample=async=1000` (hard compensation) | −0.61% | −0.68% | **−0.05 s** |
| no wall-clock timestamps | −2.87% | −7.85% | −2.99 s |
| soft compensation (`max_soft_comp`) | −4.33% | −9.22% | −2.94 s — **useless** |

Hard compensation fixes the clock. It does it by injecting ~103 ms of silence into
speech, and that **measurably damages the transcript**: on the same real 10-minute
recording, 1536 → 1506 words (2% lost), 96.1% text similarity, one domain term lost.
The control measurement matters here — whisper run twice on the *same* file produced
**identical** output, so that 2% is real loss, not model variance.

Therefore: **the audio stays raw and the timestamps are corrected afterwards**
(`src/timeline.py`). The map comes from the progress lines, is stored as
`timeline.json`, and `pipeline.do_transcribe` moves both channels' segments onto wall
time immediately before merging. Whisper always sees untouched audio.

Four rules, each from a real failure:

1. **Correcting one channel is worse than correcting neither.** They drift at different
   rates; moving one and not the other widens the gap. If both maps are not available,
   no correction is applied.
2. **A wrong map is worse than no map.** `timeline._sane()` checks that the map starts
   at the beginning of the file and covers at least 80% of it. In one real recording
   with 17 microphone parts, 16 part logs had been cleaned up and the map was shifting
   the microphone by **63 minutes**; this check caught it.
3. **Part placement is written to `mic.parts.json`** at merge time. Deriving it from the
   part logs later is not reliable, because log cleanup removes them.
4. **`meet split` and `meet trim` convert back.** You give them a time you read from the
   transcript; the audio must be cut at that time's *file* position. With no map, times
   are used unchanged, so older recordings are unaffected.

`tests/test_timeline.py` covers the mapping, the round trip, the two channels
converging (45 s → 0.1 s) and — most importantly — that recordings without a map are
left alone. `bin/_tltest.sh` verifies it end to end on a real 90-second recording.

### Do not use `aresample=async`

Field data: in one 47-minute recording, `sys.wav` had **621 mid-speech gaps**, each
**103 ms ± 2.6 ms**, 13 per minute. The file duration matched ffmpeg's real elapsed time
exactly (0.00% difference), which is the tell: the filter was padding to wall clock.

Controlled test (`bin/_droptest.sh`, continuous 440 Hz tone, same device, sequential):

| setting | gaps / 30 s | gap length |
|---|---|---|
| wallclock + `aresample=async=1000` | 38 | 102 ms |
| `aresample=async=1000`, no wallclock | 31 | 100 ms |
| `async=1000` + `min_hard_comp=1.0` | 2 | **1024 ms** |
| wallclock, **no aresample** | **0** | — |
| raw | **0** | — |

The mechanism is `min_hard_comp`, which defaults to 0.1 s. Keep
`async_compensation: false`.

---

## Process hygiene

### ffmpeg does not die without SIGKILL

Measured once: **10 orphaned ffmpeg processes** running simultaneously, the oldest **9
days old**, all holding a microphone input and writing into closed meeting folders. The
cause was `_stop_pid` escalating only SIGINT → SIGTERM; an ffmpeg stuck reading from a
Bluetooth microphone swallows both. Worse, `cmd_stop` only knew the *current* mic and
sys pids — the ones left behind by rotation were recorded nowhere.

Four defences now: `_stop_pid` escalates to SIGKILL and verifies death · `_rotate_mic`
reaps the old process · `cmd_stop` reaps every ffmpeg carrying the session path ·
`cmd_start` clears processes still writing to older meetings. Detection: `meet check`
and `meet audit`; `meet audit --fix` kills them.

**This count must always be 0.** If it is not, no microphone measurement is trustworthy
— several clients racing for the same CoreAudio input can leave a new stream with zero
samples.

What was *not* proven: that the ghosts caused the chronic "microphone is digitally
silent" diagnosis. Levels during the ghost period were healthy (−23 to −25 dBFS). The
leak was real and wasteful; the causal link was never established. Do not overstate it.

### The guard could resurrect a stopped recording

A real failure chain: `meet stop` cleared the state → in the same 4-second window the
guard mistook a dead microphone stream for digital silence and called `_rotate_mic` →
`write_state()` **brought the recording back** → the watcher saw a live recording and
stopped it again → every stop spawned a transcription process. Six processes ended up
transcribing the same meeting, each loading ~3 GB of models.

**Any new code in the guard that calls `write_state()` must re-check `read_state()`
immediately before writing.**

---

## Output device management

Returning to the previous device was a ratchet. `cmd_start` saved the device in use
before recording and `cmd_stop` returned to it — but if a recording ended uncleanly, the
device was left on the Multi-Output, so the *next* recording saved "Multi-Output" as the
previous device and "returned" there afterwards. Once stuck, it could never get out.

`audiodev.sane_idle_output()` fixes it: a Multi-Output is never treated as a previous
device; the first connected device in `idle_output_priority` is used instead. **New code
must not return blindly to `prev_output`.**

---

## Automatic start and stop

### Flapping: 67 folders from one meeting

A customer call produced **67 separate folders** in 73 minutes, each about 41 seconds.
The chain: the meeting app sent audio straight to the headset instead of the
Multi-Output → the watched OUTPUT device looked permanently idle → stop after 25 s →
microphone still held by the app → start after 15 s → a 45-second loop.

The same root cause destroyed the audio: with nothing reaching BlackHole, 67 of 70
folders had a `sys.wav` averaging −71 dBFS. The far side was never recorded.

**Do not try to solve this with a level threshold.** Measured in a real meeting: the
microphone's 45-second-window RMS had a median of **−64.9 dBFS** and a minimum of −98.
Silence and speech cannot be separated by level.

The rule used instead is threshold-free: *if we restart immediately after stopping, the
stop signal is wrong.* After `flap_max` of those, the signal is distrusted for the rest
of the session, recording is held open, and a notification is sent. Worst case becomes 3
folders instead of 67. `tests/test_flap.py` simulates exactly that scenario.

*Trap when writing such a test:* if `stop_device` is not given explicitly, the profile
lookup consults the real device list, finds nothing in a test, and stopping is disabled —
so the test passes without testing anything. That happened in the first version.

### Recording that never stops

Menu bar log over several months: **168 starts, 140 stops**. Twenty-eight recordings
were never stopped automatically. One of them, reconstructed from the guard log:

| time | event |
|---|---|
| 16:19:57 | watcher started the recording |
| 17:46:31 | microphone silent for 2 minutes (Bluetooth HFP dropped) |
| **18:13:26** | guard switched the output profile from the headset Multi-Output to the speaker one — which is exactly the moment the headset disconnected |
| 18:36:40 | the recording finally stopped, 23 minutes later, by hand |

Mechanism: the stop signal is the OUTPUT device going idle for 25 s, and which device is
watched is re-evaluated every loop. Once the headset is gone, the watched device becomes
the laptop Multi-Output, and anything that makes a sound — a notification, a browser
tab, music — keeps it busy. The 25-second gap never happens.

**Level could not have revealed this either:** the extra recorded time read −27.7 dBFS
on sys and −33.1 on mic. Real audio, not silence.

The added signal is binary and threshold-free: *did the DEVICE the recording is using
disappear from the device list* (`guard.stop_on_mic_gone`, `mic_gone_s: 90`). Two traps,
both covered by tests:

1. **Never stop over a device that was not seen at least once during the recording.**
   Device-name matching is exact; a mismatch would otherwise cut every recording after
   90 seconds.
2. **Watch the highest-priority device the recording has used, not the current one.**
   When the headset drops, `_recover_mic` moves the stream to the built-in microphone
   and the state's device name changes — follow that and you never see the disconnect.

*A measurement trap discovered here:* file modification time is **not** the end of a
recording. A recording that looked like it had run 64 minutes long turned out to have
stopped 41 minutes earlier; the late mtime came from the user trimming the files by hand
in an editor. The authoritative source is the last `time=` line in `sys.ffmpeg.log`.

### "Do not record this conversation" is a timed pause, not a duration limit

First implemented as "stop the recording after N minutes" — wrong. The actual need has
three parts and all three are required: stop the current recording, keep the watcher
from restarting it, and **turn itself back on**. `meet snooze <min|off>` does all three.

The reason the permanent switch was not the answer is worth recording, because it is a
design lesson rather than a technical one: the user did not use it because they were
afraid of forgetting to switch it back. **A control that reverts by itself gets used; an
equivalent permanent switch does not.**

### Any test that opens the microphone triggers auto-recording

The sync tests above started three unwanted recordings. Worse, killing a test left its
ffmpeg children alive, holding the microphone and triggering more. When writing a new
measurement tool: call `meet snooze <duration>` at the start, `snooze off` and `pkill`
the children at the end. `bin/_synctest.py` does this — copy the pattern.

---

## Transcription

### VAD is mandatory

In a two-channel recording each channel is silent for more than half the time, and
whisper hallucinates loops in long digital silence — in one test it repeated the same
phrase thirty times and shifted timestamps by 20 seconds.

### What costs time is the NUMBER of windows

Whisper pads every window to 30 seconds internally, so a 9-second window costs as much
as a 30-second one. Measured on a real 50-minute recording:

| `vad_join_gap_s` | windows | padded model work |
|---|---|---|
| 1.0 | 199 | 100 min |
| 3.0 | 140 | 70 min |
| **5.0** | **114** | **57 min** (chosen) |
| 8.0 | 103 | 52 min (diminishing) |

Theoretical floor is 58 windows. Going further means packing non-adjacent speech regions
into single windows and mapping timestamps back — up to 3.4x, but complex.

### Language must be decided per window

Locking a channel to one language makes whisper **translate** foreign speech instead of
transcribing it. Keep `language_detect: window`.

But short windows cannot be trusted. Measured across 86 meetings, on channels whose
other side was ≥90% one language:

| | wrongly labelled | correctly labelled |
|---|---|---|
| lines | 741 | 10,813 |
| median duration | **0.7 s** | 2.5 s |
| median words | **2** | 7 |
| ≤3 words | **75%** | 23% |
| filler sounds ("Mm-hmm") | **39%** | 0% |

At 0.7 seconds the detector is a coin flip, and when it lands wrong whisper *invents*
plausible filler in the wrong language ("Mm-hmm", "Thank you", "Welcome to our class").
The label agrees with the text, so it cannot be caught afterwards. Long windows are
fine: only 7% of mislabelled lines had 8+ words.

Fix: windows under `lang_trust_min_s` (1.5 s) are pulled to the meeting's dominant
language, derived from the **long windows of the microphone channel** — close-miked and
clean, where the loopback is compressed and noisy. This is why the microphone is
transcribed first. The language is never *locked*, and if the dominant language does not
hold `lang_prior_min_share` (70%) of the long windows, no prior is produced at all —
that is a genuinely bilingual meeting.

### The prompt leaks into the transcript

On a real 97-minute recording, whisper wrote the `initial_prompt` term list into the
transcript three times as if it had been spoken, in low-signal windows. `_is_prompt_echo`
catches it by stripping known terms and punctuation and checking whether anything of
substance remains. `tests/test_prompt_echo.py` tests both real leaks and sentences where
those terms occur naturally — **the second group matters more**, because a false
positive deletes real speech.

### A glossary never hurts, a long prompt does

See [docs/GLOSSARY.md](docs/GLOSSARY.md) for the measurements and the four rules that
each came from a correction rule causing real damage.

---

## Diarization

Use `pyannote/speaker-diarization-community-1`: CC-BY-4.0, free, fully local, runs on
MPS, and measurably better than 3.1 (AMI IHM 18.8% → 17.0%, AliMeeting 24.5% → 20.3%
DER).

**`speaker-diarization-precision-2` is a paid model that runs in pyannoteAI's cloud.**
Be careful when changing the model name.

Diarization produces hundredth-of-a-second turns at breath pauses and splits one speaker
in two; those stick wrong labels onto transcript segments. `min_turn_s` and
`merge_gap_s` clean them up.

---

## Splitting and trimming

Two meetings recorded into one file are separated **at the transcript level — no
re-transcription needed**. `transcript.json` carries `start`, `end`, `lang`, `speaker`
and `channel` on every row, so all of the metadata can be recomputed exactly.

Three design decisions, each corrected by use:

1. **The audio is cut, not copied.** `-c copy` at the split point, no re-encoding; the
   two halves together are the same size as the original. The first version did not
   touch the audio at all, which meant pointing both halves at one file — worse.
2. **The split happens exactly where you say.** A version that aligned the second half
   to its first speech was wrong: a deliberate silence at the start of the second
   meeting is part of that meeting, and skipping it moved the folder's timestamp to the
   wrong time. A second half whose transcript starts at 00:00:25 is correct behaviour.
3. **Speaker labels are never renumbered.** The same label means the same voice; the
   second half starting at SPEAKER_02 is normal.

Splitting a **part** extends the series rather than starting a new one: the front piece
stays in place, the back piece takes the next number, later parts shift up. Two traps,
both tested: the back piece must be cut **first** (the front piece is the source, and
ffmpeg cannot read and write the same file — write to a temp file and replace), and the
stale `*-transcript.raw.md` must be deleted or term correction will write the old longer
text back over the trimmed one.

Reconciliation is mandatory and automatic: the halves' segment counts and language
distributions must add up to the original's metadata, or the tool stops before writing.

---

## CoreAudio and Bluetooth traps

**The four-character code matters.** `kAudioDevicePropertyDeviceIsRunningSomewhere` is
`gone` — not `goon`; `goin` is `kAudioDevicePropertyDeviceIsRunning`, a different thing.
With the wrong code the call returns an error, and code that turns errors into `False`
concludes "no device is in use" and auto-recording silently never triggers. This
happened. `audiowatch.selftest()` now catches it at service start — **never swallow an
error in a CoreAudio read**.

**BlackHole reads exactly −180 dBFS when idle.** It is virtual, so it has no noise
floor, which means "nobody is speaking" and "no audio is reaching us at all" are
indistinguishable by level. Watching for silence produces false alarms — this was tried
and reverted. The right diagnostic is the loopback self-test: play a quiet tone at the
start of the recording and check BlackHole heard it.

**Bluetooth measurement trap.** Right after an audio path change, a headset microphone
opens as a device, reports the correct sample rate, and sends **zero samples** (−91
dBFS). Once the path settles it works fine (−28.6 dBFS). **Wait at least 20 seconds
after any device change** before trusting a measurement. This trap was fallen into once
and a wrong diagnosis was reported.

**A Bluetooth headset is two CoreAudio devices** — a 1-channel 16 kHz HFP microphone and
a 2-channel 44.1 kHz A2DP output. That is why yours appears twice in Audio MIDI Setup.

**When another app releases the microphone, HFP is torn down and our stream dies.**
Measured: the microphone fell to −94 dBFS at second 30 of a test call and the remaining
24 seconds were empty. The device stays open and delivers nothing. `guard._recover_mic`
catches this in 12 seconds and rebuilds the stream as a new part. −80 dBFS separates
digital silence (−94) from a quiet room (−50).

**Sample-rate differences between Multi-Output members are not a problem.** Switching
mid-recording between members running at 192 kHz and 44.1 kHz was tested; three test
tones recorded cleanly and ffmpeg raised nothing.

**Only one ffmpeg may open a given avfoundation device at a time.** The first version of
one experiment ran four variants in parallel and all four came out silent — that was
contention, not a measurement. Run such tests sequentially. (This is also why ghost
processes are harmful.)

---

## Measurement methodology

**Do not measure CPU with `ps -o pcpu` or `top`.** Run back to back they disagreed
wildly (0.2 / 26.0 / 0.2 / 17.2 for the same process — `ps` reports an average decaying
over the process lifetime, `top` estimates). The only reliable method is the difference
between two `ps -o time` samples, which is what `meet cpu` does.

**A brake should be a rate, not a total.** The microphone-restart quota was a fixed count
of 15. Measured on a 68-minute meeting: restarts were spread across the call, the quota
was exhausted at minute 50, and the last 9 minutes were unprotected. A fixed total
punishes long meetings, while the point of the brake is to stop a runaway loop. It is
now a rate (`max_restarts_per_window` within `restart_window_s`) with the total kept only
as an absolute ceiling. **Do not raise the ceiling on its own** — without the rate brake
that reintroduces the original risk.

**`sys.ffmpeg.log` can be half a megabyte. That is not an error.** It is entirely
`size=… time=… bitrate=…` progress, written with `\r` onto one line. Split with
`tr '\r' '\n'` before analysing it. (Those same lines are what the timeline correction
is built from, so the log is data, not noise — `meet audit` will not delete it before
`timeline.json` exists.)

**Automation that leaves no trace cannot be debugged.** A scheduled report job appeared
to be repeating work; nothing on disk could say what it had done. The fix was a one-line
log entry per run, and the diagnosis followed immediately. Any new scheduled job should
write one.

**When a file appears to change by itself, ask the user first.** Two incidents in this
project were "mysterious" automation that turned out to be the user doing something
sensible by hand. In one of them, code had been added to "repair" the filenames — which
meant the program was quietly fighting its owner. It was removed.

---

## Interface lessons

### Automation that leaves no trace cannot be debugged (again)

Two meetings out of roughly 150 ended up with audio but no transcript. The
recording was complete, the microphone parts were merged, and then nothing. The
transcription step opens its log file *before* spawning, so the absence of that
file proves the code never reached it — and that is all it proved, because
`meet stop` wrote its output nowhere: the watcher captured and discarded it, the
guard sent it to `/dev/null`.

Every stop now leaves `stop.log` in the meeting folder, traceback included, and
the transcription start is verified a few seconds later. The root cause is still
unknown and no guess was recorded; the next occurrence will say.

The companion fix is in the UI: the menu used to offer only "transcribe the last
meeting", so a skipped meeting could not be recovered without the terminal — and
that button re-transcribed a meeting that already had a transcript, loading 3 GB
of models to compete with the work the user actually wanted. It now lists meetings
that have audio but no transcript, newest first.

### An unreleased fix is an unmade fix

Pushing to `main` is not shipping. Three audiences, and the middle one is the worst:

| who | gets the fix |
|---|---|
| anyone running `git pull` | yes |
| anyone who downloaded a release archive | no — the tag still points at older code |
| anyone doing nothing | **is not even told** — the update check compares the local `VERSION` with the latest *release*, and if both still read 1.0.0 it reports "up to date" |

So `VERSION`, the changelog entry, the tag and the release have to move together.
`bin/_verifypublic.sh` now fails when `main` has commits the latest release does
not, or when `VERSION` and the release tag disagree. Without that check the same
thing recurs silently, which it already did once.

### macOS: modal dialogs only from the main thread

Moving the update check to a background thread moved its dialog there too. macOS
refused (`NSWindow should only be instantiated on the main thread`), the dialog
never appeared, and the thread died silently — visible only as a traceback in the
menu bar log. Background work now queues alerts and the timer callback (which runs
on the main thread) shows them.

A related trap: a rumps submenu cannot be `clear()`ed before it has been added to
the menu; doing it in `__init__` stops the app from starting at all.


**If a button does not do the thing, its name must say so.** The menu item that prepares
an action-item command cannot generate the report itself, because that needs an
assistant session. It used to say it did, and silently appeared to do nothing. It now
says "prepare the command" and shows a modal.

**Show recording and processing at the same time.** Back-to-back meetings mean the
previous transcription is still running while the next recording starts. The menu bar
used to show only progress, and the user reasonably concluded nothing was being
recorded.

**A per-meeting choice must not become a permanent setting.** A manually pinned language
stayed pinned for the life of the process and silently applied to the next meeting. It
now reverts when the recording ends, with a notification.

**Output files carry the meeting's timestamp in their names.** Not a technical decision
— these get emailed to colleagues, and a dozen attachments called `transcript.md`
collide. Writers use `output_path()`, readers use `find_output()` (new name first, then
the old flat name). Do not hard-code these three filenames anywhere.

---

## Open questions

- **`coreaudiod` burns 11–16% CPU while idle.** Measured with nothing playing, no device
  flagged as running and no ffmpeg alive. For comparison, our two capture processes used
  2.0% and 1.2% during a meeting — capture itself is cheap. Eliminated: a broken
  Multi-Output master (tested by switching to plain speakers: still 9.9–15.7%), our own
  processes, an app holding a stream. Next test: `killall coreaudiod` followed by
  `meet cpu`.
- **The root cause of the capture deficit** (5–18% of samples never arriving) is still
  unknown. The timeline correction makes it harmless for transcripts, but the audio is
  genuinely missing those samples.
- **Whether the ghost-ffmpeg leak ever caused microphone silence.** Plausible, never
  proven.
