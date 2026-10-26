#!/usr/bin/env python3
"""Kontrol ölçümü: whisper AYNI dosyada iki çalıştırmada ne kadar farklı sonuç verir?

Boşluk testi (`_gaptest.py`) boşluklu sürümde %2 kelime kaybı ve %96.1 benzerlik
buldu. Bu farkın BOŞLUKTAN mı yoksa modelin kendi değişkenliğinden mi geldiğini
bilmeden karar verilemez. Burada aynı dosya iki kez transkript edilip aynı
ölçütlerle karşılaştırılıyor: çıkan sayı "gürültü tabanı"dır.

Kullanım: bin/_gapnoise.py [/tmp/gaptest/duz.wav]
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common import load_config  # noqa: E402


def words(rows) -> list[str]:
    txt = " ".join(r.get("text", "") for r in rows).lower()
    return re.findall(r"[0-9a-zçğıöşü]+", txt)


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/gaptest/duz.wav")
    if not path.exists():
        print(f"HATA: {path} yok — önce bin/_gaptest.py çalıştır"); return 1
    import transcribe as T
    cfg = load_config()
    runs = []
    for i in (1, 2):
        print(f"── {i}. çalıştırma…", flush=True)
        r = T.transcribe(path, cfg)
        rows = r["rows"] if "rows" in r else r.get("segments", [])
        runs.append(rows)
        print(f"   {len(rows)} satır · {len(words(rows))} kelime")
    a, b = words(runs[0]), words(runs[1])
    sim = SequenceMatcher(None, a, b).ratio()
    print("\n" + "=" * 68)
    print(f"AYNI dosya, iki çalıştırma: {len(a)} → {len(b)} kelime "
          f"({(len(b) - len(a)) / max(1, len(a)) * 100:+.1f}%) · benzerlik {sim * 100:.1f}%")
    print("\nBu, modelin kendi gürültüsü. Boşluk testinin farkı bu bandın İÇİNDE")
    print("kalıyorsa boşlukların ölçülebilir bir zararı yok demektir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
