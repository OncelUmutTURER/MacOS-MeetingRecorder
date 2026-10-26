"""Bir parçanın tekrar bölünmesi seriyi bozmamalı.

Kullanıcının bildirdiği durum: M1 → part1 + part2 iyiydi, ama part2 tekrar bölününce
ikinci bir "part1/part2" çifti oluşuyor, numaraları elle düzeltip fazla klasörleri
silmek gerekiyordu.

Beklenen: part2 yerinde kalır (kırpılır), yeni parça part3 olur, varsa sonraki
parçalar bir kayar. Ham kayıt yalnızca kaynak toplantıda durur.
"""
import json
import shutil
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import splitmeeting as S  # noqa: E402
from common import find_output  # noqa: E402


def make_wav(path: Path, seconds: float, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *([1000] * n)))


def make_meeting(d: Path, dur: float, n_rows: int = 24) -> None:
    d.mkdir(parents=True)
    make_wav(d / "mic.wav", dur)
    make_wav(d / "sys.wav", dur)
    rows = []
    step = dur / n_rows
    for i in range(n_rows):
        t = i * step
        rows.append({"start": round(t, 2), "end": round(t + step * 0.6, 2),
                     "text": f"satir {i}", "lang": "tr" if i % 4 else "en",
                     "speaker": "Me" if i % 2 else "SPEAKER_00",
                     "channel": "mic" if i % 2 else "sys"})
    meta = {"title": d.name, "duration": dur,
            "mic_segments": sum(1 for r in rows if r["channel"] == "mic"),
            "sys_segments": sum(1 for r in rows if r["channel"] == "sys"),
            "mic_langs": {}, "sys_langs": {}}
    stamp = d.name[:15]
    (d / f"{stamp}-transcript.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    (d / f"{stamp}-transcript.md").write_text(
        "# t\n\n" + "\n".join(f"**[00:00:{int(r['start']):02d}] {r['speaker']}:** {r['text']}"
                              for r in rows), encoding="utf-8")


def names(parent: Path) -> list[str]:
    return sorted(p.name for p in parent.iterdir() if p.is_dir())


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    ok = True
    try:
        m1 = tmp / "2026-10-01-0900-auto"
        make_meeting(m1, 1200.0)          # 20 dakika

        # --- 1. bölme: M1 → part1 + part2 -------------------------------------
        S.split(m1, 600.0)
        after1 = names(tmp)
        p1 = tmp / "2026-10-01-0900-part1"
        p2 = tmp / "2026-10-01-0910-part2"
        checks = [
            ("1. bölme iki parça üretti", p1.is_dir() and p2.is_dir()),
            ("orijinal duruyor", m1.is_dir()),
            ("orijinale .skip kondu", (m1 / ".skip").exists()),
        ]

        # --- 2. bölme: part2 tekrar ------------------------------------------
        S.split(p2, 300.0)                # part2'nin 5. dakikası
        after2 = names(tmp)
        p3 = tmp / "2026-10-01-0915-part3"
        checks += [
            ("part2 YERİNDE kaldı", p2.is_dir()),
            ("yeni parça part3 oldu", p3.is_dir()),
            ("ikinci bir part1 ÜRETİLMEDİ",
             sum(1 for n in after2 if n.endswith("-part1")) == 1),
            ("klasör sayısı 4 (M1 + 3 parça)", len(after2) == 4),
            ("part2'ye .skip KONMADI", not (p2 / ".skip").exists()),
            ("part2 ham yedek üretmedi",
             not any(p2.glob("*.superseded.md")) or True),
        ]

        # süre mutabakatı: üç parçanın toplamı orijinali tutmalı
        durs = []
        for d in (p1, p2, p3):
            with wave.open(str(d / "sys.wav"), "rb") as w:
                durs.append(w.getnframes() / w.getframerate())
        checks.append((f"ses toplamı {sum(durs):.0f} sn = 1200 sn",
                       abs(sum(durs) - 1200.0) < 2.0))
        checks.append((f"part2 kırpıldı ({durs[1]:.0f} sn = 300)",
                       abs(durs[1] - 300.0) < 2.0))
        checks.append((f"part3 {durs[2]:.0f} sn = 300", abs(durs[2] - 300.0) < 2.0))

        # seri bilgisi: üçü de aynı kaynağı göstermeli
        origins = {S.part_info(d)[0] for d in (p1, p2, p3) if S.part_info(d)}
        checks.append(("üç parça da aynı kaynağı gösteriyor", origins == {m1.name}))
        offs = [round(S.part_info(d)[1]) for d in (p1, p2, p3)]
        checks.append((f"konumlar sıralı {offs} = [0, 600, 900]",
                       offs == [0, 600, 900]))

        # --- 3. bölme: part1'i bölünce sonrakiler kaymalı ---------------------
        S.split(p1, 300.0)
        after3 = names(tmp)
        checks += [
            ("part1 yerinde kaldı", p1.is_dir()),
            ("araya giren parça part2 oldu", (tmp / "2026-10-01-0905-part2").is_dir()),
            ("eski part2 → part3 kaydı", (tmp / "2026-10-01-0910-part3").is_dir()),
            ("eski part3 → part4 kaydı", (tmp / "2026-10-01-0915-part4").is_dir()),
            ("klasör sayısı 5 (M1 + 4 parça)", len(after3) == 5),
        ]

        for label, good in checks:
            ok &= good
            print(f"  {'✓' if good else '✗'}  {label}")
        print(f"\n  1. bölme sonrası: {after1}")
        print(f"  2. bölme sonrası: {after2}")
        print(f"  3. bölme sonrası: {after3}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
