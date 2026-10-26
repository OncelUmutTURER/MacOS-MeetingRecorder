# Setup

Four things cannot be scripted: the virtual audio devices, the Hugging Face token and
model licences, macOS permissions, and your own configuration. This is all of them, in
the order you should do them.

Budget about 30 minutes, most of it waiting for a 3 GB model download.

---

## 1. Dependencies

```bash
brew install blackhole-2ch ffmpeg switchaudio-osx terminal-notifier
```

| Package | Why |
|---|---|
| `blackhole-2ch` | virtual audio device; this is how the far side of a call is captured |
| `ffmpeg` | the actual recording, and all lossless cutting |
| `switchaudio-osx` | reads and switches the system output device |
| `terminal-notifier` | notifications from the guard and the watcher |

BlackHole installs an audio driver, so macOS will ask you to allow it in **System
Settings → Privacy & Security** and then to restart. Do that before continuing — the
device will not appear until you do.

Then the Python side:

```bash
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
```

This pulls torch, mlx, mlx-whisper, pyannote.audio and silero-vad. It is a few GB and
takes a while. Python 3.12 specifically: 3.13 wheels for some of these were still
missing when this was written.

---

## 2. Audio devices

This is the part everyone gets wrong, so here is what is actually happening.

A Mac can only send its audio to **one** output device. If you send it to BlackHole, the
recording works and **you hear nothing**. A *Multi-Output Device* sends the same audio
to two places at once — your headphones **and** BlackHole — so you hear the meeting and
we capture it.

You need one Multi-Output per output device you use. The example config assumes two:

| Name | Members | Used when |
|---|---|---|
| `Meeting Output BT` | BlackHole 2ch + your Bluetooth headset | the headset is connected |
| `Meeting Output SPK` | BlackHole 2ch + MacBook Pro Speakers | everything else |

### Creating them

1. Open **Audio MIDI Setup** (`/Applications/Utilities`).
2. Bottom-left **+** → **Create Multi-Output Device**.
3. Tick **BlackHole 2ch** and your headphones/speakers.
4. Rename it (double-click the name in the list) to exactly `Meeting Output BT`.
5. In the **Master Device** column, pick your *real* device — not BlackHole.
6. Tick **Drift Correction** for every member *except* the master.
7. Repeat for `Meeting Output SPK` with the built-in speakers.

Then put those names into `config.yaml`:

```yaml
audio:
  output_profiles:
    - match_input: "My Bluetooth Headset"   # exact input device name
      output: "Meeting Output BT"
    - match_input: null                     # default
      output: "Meeting Output SPK"
```

`match_input` is an **input** device name: "if this microphone is connected, use this
Multi-Output". Bluetooth headsets appear as two separate CoreAudio devices — a 1-channel
16 kHz microphone and a 2-channel 44.1 kHz output — which is why macOS shows yours
twice. Use the name as printed by:

```bash
./bin/meet devices
```

That command lists every input device with its channel count, every output device, and
tells you which profile would be selected right now.

### A trap worth knowing

If the Multi-Output's **master device** is pinned to something that is not connected,
the configuration is broken in a way nothing reports. Check it if the far side is
recorded as silence. A Multi-Output whose members run at different sample rates is
*not* a problem — that was measured, switching mid-recording stayed clean.

### Meeting application settings

Teams, Zoom and Meet each choose their own output device and **do not follow the system
default**. Set the speaker inside the app to `Meeting Output BT` / `SPK`:

- Teams: *Settings → Devices → Speaker* (Teams has no "Same as System" option)
- Zoom: *Settings → Audio → Speaker*
- Meet: the gear icon during the call → *Audio*

Get this wrong and you record yourself perfectly and the other side not at all. The
guard warns you about 3.5 minutes in, and the start-of-recording loopback self-test
catches it in the first three seconds, but it is easier to just set it once.

---

## 3. Hugging Face token and model licences

Speaker diarization downloads a gated model, so it needs a token and two licence
acceptances. Everything here is free.

1. Create an account at [huggingface.co](https://huggingface.co).
2. Accept the conditions on **both** model pages while signed in:
   - <https://huggingface.co/pyannote/speaker-diarization-community-1>
   - <https://huggingface.co/pyannote/segmentation-3.0>
3. Create a token: **Settings → Access Tokens → New token**, type **Read**.
4. Put it in a `.env` file in the project root and lock it down:

```bash
echo 'HF_TOKEN=hf_your_token_here' > .env
chmod 600 .env
```

`.env` is in `.gitignore`. `meet check` verifies both that the token exists and that the
file mode is 600.

> **`speaker-diarization-precision-2` is a paid, cloud-hosted model.** Do not put that
> name in `config.yaml`. The default, `community-1`, is CC-BY-4.0, free, fully local and
> measurably better than the old 3.1 (AMI IHM 18.8% → 17.0% DER).

Whisper needs no token. Both models are downloaded on first use into
`~/.cache/huggingface` (~3 GB total) and never again.

---

## 4. macOS permissions

Recording needs **Microphone** permission, and macOS grants it to the *executable that
starts the process*, not to the project.

- Running `meet start` from Terminal → Terminal needs the permission.
- Running the menu bar app from the launch agent → that Python binary needs it.

The first recording triggers the prompt. If you never see one and recordings come out
silent, look in **System Settings → Privacy & Security → Microphone**.

Verify with a 10-second diagnostic recording:

```bash
./bin/meet miccheck
```

A healthy microphone reads around **-25 dBFS**. Around **-90 dBFS** means digital
silence: the device is open but delivering nothing. On Bluetooth that usually means the
HFP profile was set up badly — take the headset out, put it back, **wait 20 seconds**,
and try again. That wait is not superstition: a measurement taken too soon after a
device change reported a working microphone as broken.

---

## 5. Where recordings are kept

```yaml
paths:
  meetings_dir: "~/Meetings"
```

One folder per meeting, named `YYYY-MM-DD-HHMM[-title]`. Deliberately outside the
project, so moving or reinstalling the project never touches your recordings.

Size: roughly **120 MB per hour** for the two channels together (16 kHz mono PCM each).
A year of daily meetings is a few tens of GB. `meet audit` reports what can be cleaned
up and `meet audit --fix` removes only derived files — never `mic.wav` or `sys.wav`.

---

## 6. Your configuration

```bash
cp config.example.yaml config.yaml
```

The four values you must set:

| Key | What to put |
|---|---|
| `audio.mic_priority` | your microphones, best first, built-in last |
| `audio.output_profiles` | the Multi-Output names from step 2 |
| `audio.idle_output_priority` | where to return the output device after a meeting |
| `me.label` | how you want to be labelled in transcripts |

Then the language:

```yaml
transcribe:
  language: null                      # detect automatically
  language_candidates: ["en", "de"]   # ...but only between these
```

Listing your actual languages matters. Whisper knows 99 and wanders into neighbouring
ones on short, noisy windows; this list is what stops it.

---

## 7. Verify

```bash
./bin/meet check
```

14 checks. Every line must be green before you trust a recording:

```
✓ project root · ✓ PATH tools · ✓ BlackHole · ✓ heavy dependencies
✓ project modules · ✓ CoreAudio flag · ✓ Multi-Output devices · ✓ microphone selection
✓ .env · ✓ glossary · ✓ meetings folder · ✓ ghost ffmpeg · ✓ symlink · ✓ launch agent
```

**"ghost ffmpeg" must always be 0.** If it is not, some old recording process is still
holding the microphone and no audio measurement you make is trustworthy. `meet audit
--fix` kills them.

Then do a real run: start a recording, play a YouTube video for thirty seconds, say
something, stop, and read the transcript.

```bash
./bin/meet start "test"
# ...thirty seconds...
./bin/meet stop
./bin/meet inspect        # levels: mic around -25 dBFS, sys around -25 dBFS
```

If `sys.wav` is near -70 dBFS or lower, the far side is not reaching BlackHole: go back
to step 2 and check the application's speaker setting.

---

## 8. Optional: start it with your Mac

```bash
ln -s "$PWD/bin/meet" ~/.local/bin/meet   # put `meet` on your PATH
meet install-agent                        # menu bar app at login
```

`meet uninstall-agent` removes it. The launch agent is a normal LaunchAgent plist under
`~/Library/LaunchAgents/`, with absolute paths — if you move the project folder, use
`./bin/relocate.sh /new/path` rather than moving it by hand; it rewrites the paths
inside the virtualenv, refreshes the symlink and reinstalls the agent.

---

## Automatic recording

Off by default. Turn it on from the menu bar (**Toplantıda Otomatik Kaydet**) once
everything above is verified.

It starts when the **microphone** has been in use for 15 uninterrupted seconds *and* a
meeting application is running, and stops when the **output** device has been idle for
25 seconds. Those are deliberately different devices: once recording starts, our own
ffmpeg holds the microphone, so a microphone-based stop condition would never fire.

Which applications count:

```yaml
autostart:
  require_apps:
    - "Microsoft Teams"
```

An empty list means *any* microphone use starts a recording, including dictation.

Watch it live before trusting it:

```bash
./bin/meet watch --debug
```

This prints every CoreAudio device and lights up the ones in use, so you can see
exactly which device your meeting app touches when a call starts.

---

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `sys.wav` is silent | application speaker setting; Multi-Output master device; `meet inspect` |
| Microphone silent at -90 dBFS | Bluetooth HFP — reconnect, wait 20 s, `meet miccheck` |
| Recording does not stop after the meeting | expected if you keep playing audio; `guard.stop_on_mic_gone` handles the headset case |
| Recording starts by itself when it should not | `require_apps`, or use **Otomatik Kaydı Duraklat** / `meet snooze 30` |
| Two meetings in one recording | `meet split [folder] --suggest`, then `--at <time>` |
| Recording ran on past the end | `meet trim [folder] --to <time>` |
| Transcription is very slow | check `meet audit` for ghost processes first; then `vad_join_gap_s` |
| Odd timestamps, channels out of step | `meet inspect` reports the drift; see ENGINEERING-NOTES.md |

`meet inspect [folder]` is the single most useful diagnostic: channel levels, an RMS
profile over time (so you can see exactly where a channel went dead), the guard log,
dropout counts, and the device formats ffmpeg actually opened.
