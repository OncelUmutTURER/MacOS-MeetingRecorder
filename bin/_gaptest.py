#!/usr/bin/env python3
"""103 ms'lik telafi boşlukları transkript DOĞRULUĞUNU bozuyor mu?

NEDEN: iki kanalın birbirinden kayması (dakikada ~2.8 sn) ancak `aresample`
telafisiyle düzeliyor, ama telafi konuşmanın ortasına ~103 ms sessizlik
enjekte ediyor. Kullanıcının önceliği net: "transcript'in doğruluğu highest
priority, ses kaydının insan tarafından anlaşılır olması düşük öncelik."
Yani karar kulakla değil ÖLÇÜMLE verilmeli.

YÖNTEM: gerçek bir toplantının sesinden bir dilim alınır ve İKİ sürümü
transkript edilir:
    düz    — olduğu gibi
    boşluklu — her `period` saniyede bir `gap_ms` kadar sessizlik enjekte edilmiş
               (saha ölçümü: dakikada 6-13 boşluk, her biri 103±2.6 ms)
Aynı model, aynı ayarlar, SIRAYLA. Sonra iki metin karşılaştırılır:
kelime sayısı, ortak kelime oranı, sözlükteki terimlerin sayısı ve
halüsinasyon/kayıp belirtileri.

Kullanım:
    bin/_gaptest.py <toplanti_klasoru> [başlangıç_sn] [süre_sn]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import wave
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common import load_config  # noqa: E402

OUT = Path("/tmp/gaptest")
GAP_MS = 103          # ölçülen boşluk süresi
PERIOD_S = 4.6        # dakikada ~13 boşluk — saha ölçümünün ÜST ucu (en zor hâl)

# Kendi jargonunla değiştir: ölçüm, bu terimlerin iki sürümde de aynı sayıda
# geçip geçmediğine bakıyor.
TERMS = ["ladle", "refractory", "tundish", "slagline", "caster", "campaign",
         "pulpit", "hot spot", "laser scan", "PLC", "SCADA", "furnace"]


def cut(src: Path, dst: Path, start: float, dur: float) -> None:
    subprocess.run(["/opt/homebrew/bin/ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-y", "-ss", str(start), "-i", str(src), "-t", str(dur),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
                   check=True)


def inject_gaps(src: Path, dst: Path, gap_ms: int, period_s: float) -> int:
    """Her period_s saniyede bir gap_ms kadar dijital sessizlik ekler."""
    with wave.open(str(src), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    step = int(period_s * sr) * 2          # bayt
    gap = b"\x00" * (int(gap_ms / 1000 * sr) * 2)
    out, i, count = bytearray(), 0, 0
    while i < len(raw):
        out += raw[i:i + step]
        i += step
        if i < len(raw):
            out += gap
            count += 1
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(bytes(out))
    return count


def words(rows: list[dict]) -> list[str]:
    txt = " ".join(r.get("text", "") for r in rows).lower()
    return re.findall(r"[0-9a-zçğıöşü]+", txt)


def term_hits(rows: list[dict]) -> dict[str, int]:
    txt = " ".join(r.get("text", "") for r in rows).lower()
    return {t: txt.count(t.lower()) for t in TERMS if txt.count(t.lower())}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    session = Path(sys.argv[1]).expanduser()
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
    src = session / "sys.wav"
    if not src.exists():
        print(f"HATA: {src} yok"); return 1

    OUT.mkdir(parents=True, exist_ok=True)
    plain, gapped = OUT / "duz.wav", OUT / "bosluklu.wav"
    print(f"kaynak : {session.name}  [{start:.0f} – {start + dur:.0f} sn]")
    cut(src, plain, start, dur)
    n = inject_gaps(plain, gapped, GAP_MS, PERIOD_S)
    print(f"boşluk : {n} adet × {GAP_MS} ms  (dakikada {n / (dur / 60):.0f}) "
          f"→ toplam {n * GAP_MS / 1000:.1f} sn eklendi\n")

    import transcribe as T
    cfg = load_config()
    res = {}
    for tag, path in (("düz", plain), ("boşluklu", gapped)):
        print(f"── {tag} transkript ediliyor…", flush=True)
        r = T.transcribe(path, cfg)
        rows = r["rows"] if isinstance(r, dict) and "rows" in r else r.get("segments", [])
        res[tag] = rows
        (OUT / f"{tag}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(f"   {len(rows)} satır · {len(words(rows))} kelime")

    a, b = words(res["düz"]), words(res["boşluklu"])
    sim = SequenceMatcher(None, a, b).ratio()
    ta, tb = term_hits(res["düz"]), term_hits(res["boşluklu"])
    print("\n" + "=" * 70)
    print(f"kelime sayısı   : düz {len(a)}  ·  boşluklu {len(b)}  "
          f"({(len(b) - len(a)) / max(1, len(a)) * 100:+.1f}%)")
    print(f"metin benzerliği: {sim * 100:.1f}%   (1.0 = birebir aynı)")
    print(f"satır sayısı    : düz {len(res['düz'])}  ·  boşluklu {len(res['boşluklu'])}")
    print(f"\nsözlük terimleri (düz → boşluklu):")
    for t in sorted(set(ta) | set(tb)):
        da, db = ta.get(t, 0), tb.get(t, 0)
        mark = " " if da == db else ("↓" if db < da else "↑")
        print(f"   {mark} {t:<14} {da:3} → {db:3}")
    print("\nOKUMA: benzerlik %95+ ve terim sayıları aynıysa boşlukların transkript")
    print("doğruluğuna etkisi ölçülebilir değil — senkronu seçmek bedava demektir.")
    print(f"\nmetinler: {OUT}/duz.json · {OUT}/bosluklu.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
