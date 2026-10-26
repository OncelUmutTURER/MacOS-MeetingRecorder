#!/usr/bin/env python3
"""İki kanalın zaman ekseni duvar saatini tutuyor mu? — kontrollü ölçüm.

SAHA BULGUSU (2026-10-21, 119 kayıt): `aresample` filtresi 2026-09-16'da
kaldırıldıktan sonra `sys.wav`, ffmpeg'in loguna yazdığı duvar saatinden
**medyan %6.4 KISA** çıkıyor; filtre varken sapma %0.00'dı. `mic.wav` ise
parçalardan mutlak epoch'a göre diziliyor, yani duvar saatini tutuyor.
Sonuç: iki kanal birbirinden kayıyor ve birleşik transkriptte konuşmalar
yanlış sıraya giriyor.

Bu test sebebi ayırır. Her varyantta kayıt SABİT DUVAR SÜRESİ boyunca sürer
(`-t` ile kesilmez, SIGINT ile durur) ve iki bölümden oluşur:

    0 – QUIET sn     hiçbir şey çalmıyor (BlackHole'a veri gitmiyor)
    QUIET – sonu     440 Hz ton çalıyor

İki soru birden cevaplanır:
  · dosya süresi ÷ duvar süresi      → zaman kaybı var mı
  · tonun dosya içindeki başlangıcı  → kayıp SESSİZLİKTE mi oluşuyor
    (QUIET'ten erken başlıyorsa sessiz bölüm sıkışmış demektir — yani
    BlackHole boştayken örnek üretmiyor ve o süre dosyadan düşüyor)

Varyantlar SIRAYLA çalışır: aynı avfoundation cihazını iki ffmpeg aynı anda
açamaz (bir kez dört varyant paralel çalıştırıldı ve dördü de sessiz çıktı —
ölçüm değil çakışmaydı).

Kullanım:  bin/_synctest.sh [saniye]      (varsayılan 40+40, ~4 dakika)
"""
from __future__ import annotations

import math
import re
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FFMPEG = "/opt/homebrew/bin/ffmpeg"
SWITCH = "/opt/homebrew/bin/SwitchAudioSource"
OUT = Path("/tmp/synctest")
WSR = "16000"

ALL_VARIANTS = {
    "A": ("A-bugun", ["-use_wallclock_as_timestamps", "1"], ["-ar", WSR],
          "bugünkü ayar: duvar saati damgası, aresample yok"),
    "B": ("B-aresample", ["-use_wallclock_as_timestamps", "1"],
          ["-af", f"aresample=async=1000:first_pts=0:osr={WSR}"],
          "eski ayar: duvar saati damgası + sert telafi (sessizlik enjekte eder)"),
    "C": ("C-ham", [], ["-ar", WSR],
          "duvar saati damgası YOK (cihaz saati), aresample yok"),
    # D: cihazın gerçek hızını GİRİŞ seçeneği olarak dayat. Hipotez: avfoundation
    # bildirdiği hızda örnek üretmiyor ve açık kayıp oradan geliyor.
    "D": ("D-giris44100", ["-use_wallclock_as_timestamps", "1", "-ar", "44100"],
          ["-ar", WSR], "girişe 44100 dayatılmış, aresample yok"),
    # E: YUMUŞAK telafi. Sert telafi (min_hard_comp 0.1 sn) konuşmanın ortasına
    # 103 ms sessizlik enjekte ediyordu ve kullanıcı bunu duydu. Burada sert
    # telafi fiilen kapalı (10 sn), fark yavaşça yeniden örneklenerek kapatılıyor
    # — duyulmaz, ama duvar saati tutulur.
    "E": ("E-yumusak", ["-use_wallclock_as_timestamps", "1"],
          ["-af", f"aresample=async=1000:min_hard_comp=10:max_soft_comp=0.05:"
                  f"min_comp=0.001:comp_duration=1:first_pts=0:osr={WSR}"],
          "yumuşak telafi: sessizlik enjekte etmeden duvar saatini tut"),
}
VARIANTS = [ALL_VARIANTS[k] for k in "ABCDE"]


def dev_index(name: str) -> str | None:
    r = subprocess.run([FFMPEG, "-hide_banner", "-f", "avfoundation",
                        "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if name in line and (m := re.search(r"\[(\d+)\]", line.split("]")[-2] + "]")):
            return m.group(1)
    for line in r.stderr.splitlines():
        if name in line and (m := re.search(r"\[(\d+)\]\s*" + re.escape(name), line)):
            return m.group(1)
    return None


def wav_info(p: Path) -> tuple[float, int]:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate(), w.getframerate()


def tone_onset(p: Path, win: float = 0.25) -> float | None:
    """Tonun dosyadaki başlangıcı: ilk belirgin enerji sıçraması."""
    with wave.open(str(p), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    step = int(sr * win)
    if step < 100:
        return None
    levels = []
    for i in range(0, len(raw) // 2 - step, step):
        vals = struct.unpack(f"<{step}h", raw[i * 2:(i + step) * 2])
        acc = sum((v / 32768.0) ** 2 for v in vals)
        levels.append(20 * math.log10(max((acc / step) ** 0.5, 1e-9)))
    if not levels:
        return None
    floor = sorted(levels)[len(levels) // 10]          # alt %10 = sessizlik tabanı
    peak = sorted(levels)[-len(levels) // 10]          # üst %10 = ton
    if peak - floor < 12:
        return None                                     # ton ayırt edilemiyor
    thresh = floor + (peak - floor) * 0.5
    for i, lv in enumerate(levels):
        if lv > thresh and all(x > thresh for x in levels[i:i + 3]):
            return i * win
    return None


def run(tag: str, pre: list[str], post: list[str], note: str,
        sys_idx: str, mic_idx: str, quiet: float, tone: float) -> dict:
    print(f"\n── {tag}  ({note})", flush=True)
    s, m = OUT / f"{tag}-sys.wav", OUT / f"{tag}-mic.wav"
    procs = []
    t0 = time.time()
    for idx, out in ((sys_idx, s), (mic_idx, m)):
        cmd = ([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + pre +
               ["-f", "avfoundation", "-i", f":{idx}"] + post +
               ["-ac", "1", "-c:a", "pcm_s16le", str(out)])
        procs.append(subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL))
    print(f"   {quiet:.0f} sn sessizlik…", flush=True)
    time.sleep(quiet)
    print(f"   {tone:.0f} sn ton…", flush=True)
    t_tone = time.time() - t0
    player = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=44100", "-t", str(tone),
         "-af", "volume=0.05", "-f", "audiotoolbox", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(tone)
    for p in procs:
        p.send_signal(signal.SIGINT)
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    wall = time.time() - t0
    player.kill()

    res = {"tag": tag, "wall": wall, "tone_at": t_tone}
    print(f"   duvar süresi: {wall:.1f} sn · ton {t_tone:.1f}. saniyede başladı")
    for label, p in (("sys", s), ("mic", m)):
        if not p.exists():
            print(f"   {label}: dosya yok"); continue
        d, sr = wav_info(p)
        on = tone_onset(p)
        res[label] = {"dur": d, "onset": on}
        dev = (d - wall) / wall * 100
        line = (f"   {label}: {d:7.2f} sn  ({dev:+5.2f}% duvar saatine göre)")
        if on is not None:
            line += f"   ton dosyada {on:6.2f}. sn   (gerçek {t_tone:.1f} → kayma {on - t_tone:+.2f} sn)"
        print(line)
    if "sys" in res and "mic" in res:
        res["desync"] = res["mic"]["dur"] - res["sys"]["dur"]
        print(f"   → iki kanal arası süre farkı: {res['desync']:+.2f} sn")
    return res


def main() -> int:
    if (ROOT / ".state.json").exists():
        print("HATA: kayıt sürüyor, test yapılmıyor."); return 1
    quiet = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
    tone = quiet
    if len(sys.argv) > 2:                    # sadece seçilen varyantlar: "DE"
        global VARIANTS
        VARIANTS = [ALL_VARIANTS[k] for k in sys.argv[2].upper()
                    if k in ALL_VARIANTS]
    OUT.mkdir(parents=True, exist_ok=True)

    # BU TEST MİKROFONU AÇIYOR — otomatik kayıt onu toplantı sanıp kayda başlar.
    # 2026-10-21'de tam bu oldu: test iki kez yanlış kayıt tetikledi, üstelik
    # test öldürülünce ffmpeg çocukları hayatta kalıp mikrofonu açık tuttukları
    # için tetiklemeye devam ettiler. İkisinin de çaresi burada.
    total = (quiet + tone) * len(VARIANTS) + 60
    subprocess.run([str(ROOT / "bin" / "meet"), "snooze", f"{total / 60:.0f}"],
                   capture_output=True)
    print(f"otomatik kayıt {total / 60:.0f} dk duraklatıldı (test mikrofonu açıyor)")

    sys_idx, mic_idx = dev_index("BlackHole 2ch"), dev_index("MacBook Pro Microphone")
    if not sys_idx or not mic_idx:
        print(f"HATA: cihaz bulunamadı (BlackHole={sys_idx}, mic={mic_idx})"); return 1

    prev = subprocess.run([SWITCH, "-c", "-t", "output"],
                          capture_output=True, text=True).stdout.strip()
    target = "Meeting Output SPK"
    print(f"BlackHole [{sys_idx}] · mikrofon [{mic_idx}]")
    print(f"çıkış: {prev} → {target}   (test bitince geri alınacak)")
    subprocess.run([SWITCH, "-s", target, "-t", "output"], capture_output=True)
    time.sleep(2)

    results = []
    try:
        for tag, pre, post, note in VARIANTS:
            results.append(run(tag, pre, post, note, sys_idx, mic_idx, quiet, tone))
            time.sleep(2)
    finally:
        subprocess.run([SWITCH, "-s", prev, "-t", "output"], capture_output=True)
        subprocess.run(["pkill", "-f", "lavfi -i sine"], capture_output=True)
        # Çocuk ffmpeg'ler ÖLMELİ. Test öldürüldüğünde hayatta kalıp mikrofonu
        # açık tuttular ve otomatik kayıt tetiklenip durdu.
        subprocess.run(["pkill", "-f", str(OUT)], capture_output=True)
        subprocess.run([str(ROOT / "bin" / "meet"), "snooze", "off"],
                       capture_output=True)
        print(f"\nçıkış geri alındı: {prev} · otomatik kayıt yeniden aktif")

    print("\n" + "=" * 78)
    print(f"{'varyant':<14} {'sys sapma':>10} {'mic sapma':>10} {'kanal farkı':>12} "
          f"{'ton kayması (sys)':>18}")
    for r in results:
        if "sys" not in r or "mic" not in r:
            continue
        sd = (r["sys"]["dur"] - r["wall"]) / r["wall"] * 100
        md = (r["mic"]["dur"] - r["wall"]) / r["wall"] * 100
        on = r["sys"]["onset"]
        shift = f"{on - r['tone_at']:+.2f} sn" if on is not None else "ton bulunamadı"
        print(f"{r['tag']:<14} {sd:+9.2f}% {md:+9.2f}% {r['desync']:+11.2f} sn {shift:>18}")
    print("\nOkuma: 'sys sapma' sıfıra ne kadar yakınsa kanal o kadar duvar saatini")
    print("tutuyor. 'ton kayması' eksiyse sessiz bölüm sıkışmış demektir — o kadar")
    print("saniye dosyadan düşmüş ve o noktadan sonrası erkene kaymış.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
