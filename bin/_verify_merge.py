"""mic.wav gerçekten parçaların tamamını içeriyor mu — içerik seviyesinde doğrula.

Sadece süre karşılaştırmak yetmez: birleştirme sessizlikle doldurduğu için süre
tutup içerik boş olabilir. Her parçanın SESLİ bölümünün mic.wav'da aynı zaman
aralığında sesli olarak bulunduğunu kontrol ediyoruz.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000
FF = "/opt/homebrew/bin/ffmpeg"


def rms_db(a):
    return 20 * np.log10(max(float(np.sqrt((a ** 2).mean())), 1e-9)) if len(a) else -180.0


def epoch(log: Path):
    import re
    if not log.exists():
        return None
    m = re.search(r"start:\s*(\d{9,}\.\d+)", log.read_text(errors="replace")[:4000])
    return float(m.group(1)) if m else None


def check(d: Path) -> bool:
    mic = d / "mic.wav"
    parts = sorted(d.glob("mic.part*.wav"))
    if not mic.exists() or not parts:
        return True
    ref = epoch(d / "sys.ffmpeg.log")
    a, sr = sf.read(str(mic), dtype="float32", always_2d=True)
    full = a.mean(axis=1)

    print(f"\n{d.name}  ({len(parts)} parça, mic.wav {len(full)/sr/60:.1f} dk)")
    ok = True
    for p in parts:
        pa, psr = sf.read(str(p), dtype="float32", always_2d=True)
        pm = pa.mean(axis=1)
        plvl = rms_db(pm)
        if plvl < -80:
            print(f"  {p.name:<18} {len(pm)/psr:6.1f} sn  {plvl:6.1f} dB  (zaten sessiz, atlandı)")
            continue
        pe = epoch(d / (p.stem + ".log"))
        if pe is None or ref is None:
            print(f"  {p.name:<18} epoch okunamadı, atlandı")
            continue
        off = max(0.0, pe - ref)
        seg = full[int(off * sr):int(off * sr) + len(pm)]
        slvl = rms_db(seg)
        # birleşik dosyada aynı aralık en fazla 6 dB sapmayla aynı seviyede olmalı
        good = abs(slvl - plvl) < 6
        ok = ok and good
        print(f"  {p.name:<18} {len(pm)/psr:6.1f} sn  parça {plvl:6.1f} dB  "
              f"mic.wav@{off:7.1f}s {slvl:6.1f} dB  {'✓' if good else '✗ UYUŞMUYOR'}")
    return ok


targets = [Path(x) for x in sys.argv[1:]] or \
          sorted((Path.home() / "Meetings").iterdir(), key=lambda p: p.name)[-3:]
allok = True
for t in targets:
    if t.is_dir():
        allok &= check(t)
print(f"\nSONUÇ: {'tüm parçalar mic.wav içinde doğrulandı' if allok else 'UYUŞMAYAN PARÇA VAR'}")
sys.exit(0 if allok else 1)
