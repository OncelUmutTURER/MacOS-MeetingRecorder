"""`meet miccheck` — mikrofon kanalını tek başına doğrular.

10 saniye kaydeder, sonra seviye / spektral denge / VAD / transkript sonucunu raporlar.
Toplantı öncesi 'mikrofonum düzgün mü' kontrolü için.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from common import FFMPEG, find_device, load_config
from transcribe import SR, _load_audio, _rms_dbfs

SECONDS = 10


def spectral_split(a: np.ndarray) -> tuple[float, float]:
    """(<300 Hz oranı, 300-3400 Hz konuşma bandı oranı)"""
    from numpy.fft import rfft, rfftfreq
    spec = np.abs(rfft(a * np.hanning(len(a))))
    freq = rfftfreq(len(a), 1 / SR)
    tot = float(spec.sum()) or 1e-9
    low = float(spec[freq < 300].sum()) / tot
    voice = float(spec[(freq >= 300) & (freq < 3400)].sum()) / tot
    return low, voice


def main() -> None:
    cfg = load_config()
    a_cfg = cfg["audio"]
    name = a_cfg["mic_device"] if a_cfg["mode"] == "dual" else a_cfg["capture_device"]
    idx = find_device(name)

    auto = "--auto" in sys.argv        # menü çubuğundan çağrıldığında Enter beklemez
    tmp = Path(tempfile.mkdtemp()) / "miccheck.wav"
    print(f"Mikrofon: [{idx}] {name}")
    print(f"\n  {SECONDS} saniye boyunca NORMAL TOPLANTI SESİYLE konuş.")
    print("  Öneri: \"Bir iki üç dört beş. Toplantıda konuştuğun gibi normal hızda oku.\"")
    if auto:
        print("\n  Kayıt 2 saniye sonra başlıyor...")
        time.sleep(2)
    else:
        input("\n  Hazır olunca Enter'a bas...")
    print()

    proc = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-y", "-f", "avfoundation", "-i", f":{idx}",
         "-t", str(SECONDS), "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(tmp)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(SECONDS, 0, -1):
        print(f"\r  ● kayıt... {i:2d} ", end="", flush=True)
        try:
            proc.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            pass
    proc.wait()
    print("\r  kayıt bitti.        \n")

    if not tmp.exists() or tmp.stat().st_size < 2048:
        sys.exit("HATA: kayıt oluşmadı. Terminal'in mikrofon izni var mı?\n"
                 "  Sistem Ayarları > Gizlilik ve Güvenlik > Mikrofon")

    raw = _load_audio(tmp, highpass_hz=0)
    filt = _load_audio(tmp, highpass_hz=float(cfg["transcribe"].get("highpass_hz", 80)))
    low, voice = spectral_split(filt)
    rms, peak = _rms_dbfs(filt), 20 * np.log10(max(float(np.abs(filt).max()), 1e-9))

    print("  ── Ölçümler ─────────────────────────────────")
    print(f"  Ortalama seviye : {rms:6.1f} dBFS")
    print(f"  Tepe seviye     : {peak:6.1f} dBFS")
    print(f"  Gümbürtü (<300Hz): {low*100:5.1f} %")
    print(f"  Konuşma bandı    : {voice*100:5.1f} %  (300-3400 Hz)")
    print(f"  Yüksek geçiren filtrenin kazandırdığı: {_rms_dbfs(raw) - rms:+.1f} dB gürültü azalması")

    from silero_vad import get_speech_timestamps, load_silero_vad
    import torch
    ts = get_speech_timestamps(torch.from_numpy(filt), load_silero_vad(),
                               sampling_rate=SR, return_seconds=True)
    speech = sum(t["end"] - t["start"] for t in ts)
    print(f"  VAD              : {len(ts)} bölge, {speech:.1f}/{SECONDS} sn konuşma")

    print("\n  ── Transkript ───────────────────────────────")
    if not ts:
        print("  (VAD konuşma bulamadı)")
    else:
        import mlx_whisper
        import transcribe as T
        opts = {"path_or_hf_repo": cfg["transcribe"]["model"],
                "initial_prompt": T.build_prompt(cfg),
                "condition_on_previous_text": False, "verbose": None}
        if cfg["transcribe"].get("force_language") and cfg["transcribe"].get("language"):
            opts["language"] = cfg["transcribe"]["language"]
        r = mlx_whisper.transcribe(filt, **opts)
        print("  " + (" ".join(s["text"].strip() for s in r["segments"]) or "(boş)"))

    print("\n  ── Değerlendirme ────────────────────────────")
    ok = True
    if rms < -45:
        print("  ✗ Çok kısık. Mikrofona yaklaş veya giriş seviyesini artır\n"
              "    (Sistem Ayarları > Ses > Giriş).")
        ok = False
    if peak > -1.0:
        print("  ✗ Kırpma riski. Giriş seviyesini düşür.")
        ok = False
    if voice < 0.25:
        print("  ✗ Enerji konuşma bandında değil — büyük ihtimalle gürültü kaydediliyor\n"
              "    (masa darbesi, klavye, fan). Mikrofonu titreşimden uzaklaştır.")
        ok = False
    if speech < SECONDS * 0.3:
        print("  ✗ VAD konuşmanın çoğunu bulamadı.")
        ok = False
    if ok:
        print("  ✓ Mikrofon kanalı sağlıklı görünüyor.")
    print()


if __name__ == "__main__":
    main()
