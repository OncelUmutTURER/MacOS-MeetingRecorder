"""VAD pencere paketlemesinin maliyetini ölçer.

Whisper her pencereyi içeride 30 saniyeye padler; 9 saniyelik pencere 30 saniyelik
kadar maliyetli. Dolayısıyla önemli olan konuşma süresi değil PENCERE SAYISI.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np                                    # noqa: E402
from common import load_config                        # noqa: E402
from transcribe import SR, _load_audio                # noqa: E402

WAV = Path(sys.argv[1] if len(sys.argv) > 1 else
           Path.home() / "Meetings/2026-09-08-1713-auto/sys.wav")

cfg = load_config()
audio = _load_audio(WAV, float(cfg["transcribe"].get("highpass_hz", 80)))
total_s = len(audio) / SR

from silero_vad import get_speech_timestamps, load_silero_vad   # noqa: E402
import torch                                                     # noqa: E402

v = cfg["transcribe"]
regions = get_speech_timestamps(
    torch.from_numpy(audio), load_silero_vad(), sampling_rate=SR,
    min_speech_duration_ms=250,
    min_silence_duration_ms=int(v.get("vad_min_silence_ms", 500)),
    speech_pad_ms=int(v.get("vad_pad_ms", 300)),
    return_seconds=True)
speech = sum(r["end"] - r["start"] for r in regions)
print(f"{WAV.name}: {total_s/60:.0f} dk kayıt · {len(regions)} konuşma bölgesi · "
      f"{speech/60:.0f} dk konuşma ({100*speech/total_s:.0f}%)\n")


def pack(join_gap: float, max_len: float):
    chunks = []
    for r in regions:
        s, e = float(r["start"]), float(r["end"])
        if chunks and s - chunks[-1][1] <= join_gap and e - chunks[-1][0] <= max_len:
            chunks[-1][1] = e
        else:
            chunks.append([s, e])
    return chunks


print(f"{'join_gap':>9} {'max_len':>8} {'pencere':>8} {'ort.uzunluk':>12} "
      f"{'padli maliyet':>14} {'hız kazancı':>12}")
print("─" * 70)
base = None
for gap in (1.0, 2.0, 3.0, 5.0, 8.0):
    for mx in (30.0,):
        ch = pack(gap, mx)
        avg = sum(e - s for s, e in ch) / len(ch)
        cost = len(ch) * 30 / 60           # dakika cinsinden padli model işi
        if base is None:
            base = cost
        print(f"{gap:9.1f} {mx:8.0f} {len(ch):8d} {avg:11.1f}s {cost:12.0f} dk "
              f"{base/cost:11.2f}x")

# Teorik alt sınır: konuşmayı 30 sn'lik pencerelere sıkıştırsak
print("─" * 70)
print(f"teorik alt sınır (sadece konuşma, 30 sn'lik pencereler): "
      f"{int(np.ceil(speech/30))} pencere · {np.ceil(speech/30)*30/60:.0f} dk")
