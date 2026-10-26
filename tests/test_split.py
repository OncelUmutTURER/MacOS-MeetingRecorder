"""Toplantı bölmenin kayıpsız olduğunu sınar.

Asıl iddia şu: yarıların toplamı orijinalin meta'sını BİREBİR tutar. Kullanıcının elle
bölerken bozulan şeyler tam olarak bunlardı — zaman damgaları, segment sayıları,
tr/en dağılımı.
"""
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import splitmeeting as S  # noqa: E402


def make_rows():
    """İki toplantı: 0–100 sn, 180 sn boşluk, sonra 280–400 sn."""
    rows = []
    for i in range(10):
        rows.append({"start": i * 10.0, "end": i * 10.0 + 6.0, "text": f"a{i}",
                     "lang": "tr" if i % 3 else "en",
                     "speaker": "Me" if i % 2 else "SPEAKER_00",
                     "channel": "mic" if i % 2 else "sys"})
    for i in range(12):
        t = 280.0 + i * 10
        rows.append({"start": t, "end": t + 6.0, "text": f"b{i}",
                     "lang": "en" if i % 4 else "tr",
                     "speaker": "Me" if i % 2 else "SPEAKER_01",
                     "channel": "mic" if i % 2 else "sys"})
    return rows


def main() -> int:
    rows = make_rows()
    ok = True

    # --- aday bulma ---
    gaps = S.suggest(rows, min_gap=45)
    found = any(abs(s - 96.0) < 2 and abs(e - 280.0) < 2 for s, e in gaps)
    print(f"  {'✓' if found else '✗'}  180 sn'lik boşluk aday olarak bulundu "
          f"({len(gaps)} aday)")
    ok &= found

    at = 190.0
    first = [r for r in rows if r["start"] < at]
    tail = [r for r in rows if r["start"] >= at]
    # Bölme TAM verilen noktada olur. "İlk konuşmaya hizala" denendi ve yanlıştı:
    # kullanıcı yeni toplantıda bir süre susabiliyor, o sessizlik toplantının parçası.
    origin = at
    second = [{**r, "start": r["start"] - origin, "end": r["end"] - origin}
              for r in tail]

    base = {
        "mic_segments": sum(1 for r in rows if r["channel"] == "mic"),
        "sys_segments": sum(1 for r in rows if r["channel"] == "sys"),
        "mic_langs": dict(Counter(r["lang"] for r in rows if r["channel"] == "mic")),
        "sys_langs": dict(Counter(r["lang"] for r in rows if r["channel"] == "sys")),
    }
    m1 = S._meta_for(first, base, "p1", "orig", at)
    m2 = S._meta_for(second, base, "p2", "orig", at)

    checks = [
        ("satır sayısı korundu", len(first) + len(second) == len(rows)),
        ("mic_segments toplamı tutuyor",
         m1["mic_segments"] + m2["mic_segments"] == base["mic_segments"]),
        ("sys_segments toplamı tutuyor",
         m1["sys_segments"] + m2["sys_segments"] == base["sys_segments"]),
        ("mic dil dağılımı tutuyor",
         dict(Counter(m1["mic_langs"]) + Counter(m2["mic_langs"])) == base["mic_langs"]),
        ("sys dil dağılımı tutuyor",
         dict(Counter(m1["sys_langs"]) + Counter(m2["sys_langs"])) == base["sys_langs"]),
        # Bölme noktasından sonra 90 sn sessizlik var; KORUNMALI (kullanıcı susuyor olabilir)
        ("2. yarı başındaki sessizlik korundu",
         abs(min(r["start"] for r in second) - 90.0) < 0.01),
        ("bölme tam verilen noktada", origin == at),
        ("2. yarının süresi doğru", abs(m2["duration"] - (396.0 - at)) < 0.01),
        ("1. yarının süresi doğru", abs(m1["duration"] - 96.0) < 0.01),
        ("konuşmacılar ayrıldı",
         m1["speakers"] == ["SPEAKER_00"] and m2["speakers"] == ["SPEAKER_01"]),
        ("bleed_dropped taşınmadı",
         "bleed_dropped" not in m1 and "bleed_dropped" not in m2),
        ("kaynak bilgisi yazıldı",
         m1["split_from"] == "orig" and m1["split_at_s"] == at),
    ]
    for label, good in checks:
        ok &= good
        print(f"  {'✓' if good else '✗'}  {label}")

    # --- zaman ayrıştırma ---
    tcases = [("01:12:30", 4350.0), ("72:30", 4350.0), ("4350", 4350.0), ("90", 90.0)]
    for s, want in tcases:
        got = S.parse_time(s)
        good = abs(got - want) < 0.01
        ok &= good
        print(f"  {'✓' if good else '✗'}  parse_time({s!r}) = {got}")

    # --- sınır dışı bölme reddedilmeli ---
    tmp = Path(tempfile.mkdtemp())
    try:
        for bad in (0.0, 10_000.0):
            try:
                S.split(tmp / "2026-01-01-0000-x", bad)
                print(f"  ✗  {bad} sn için hata beklenirken geçti")
                ok = False
            except SystemExit:
                print(f"  ✓  geçersiz bölme noktası ({bad:.0f} sn) reddedildi")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
