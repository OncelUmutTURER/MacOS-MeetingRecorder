"""`meet trim` — kaydın kuyruğunu kırpmak sesi ve transkripti birlikte tutmalı.

Kullanıcı kayıt kendiliğinden durmadığı için bazı wav'ları ELLE kırpıyordu; elle
kırpınca transkriptteki süre, segment sayıları ve dil dağılımı tutarsız kalıyor.

Sınananlar:
  - ses gerçekten kısalıyor ve iki kanal da aynı pencereye geliyor
  - pencere dışındaki satırlar düşüyor, kalanların zamanı kaydırılıyor
  - meta satırlardan YENİDEN hesaplanıyor (süre, segment, dil)
  - eski `*-transcript.raw.md` siliniyor (yoksa fixterms eski uzun metni geri yazar)
  - eski rapor `.superseded.md` oluyor
  - --dry-run hiçbir şeye dokunmuyor
"""
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import trimmeeting as T  # noqa: E402
from common import find_output  # noqa: E402


def make_wav(path: Path, seconds: float, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *([1200] * n)))


def dur(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def make_meeting(d: Path, total: float, n_rows: int = 20) -> None:
    d.mkdir(parents=True)
    make_wav(d / "mic.wav", total)
    make_wav(d / "sys.wav", total)
    rows, step = [], total / n_rows
    for i in range(n_rows):
        t = i * step
        rows.append({"start": round(t, 2), "end": round(t + step * 0.5, 2),
                     "text": f"satir {i}", "lang": "tr" if i % 5 else "en",
                     "speaker": "Me" if i % 2 else "SPEAKER_00",
                     "channel": "mic" if i % 2 else "sys"})
    meta = {"title": d.name, "duration": total,
            "mic_segments": sum(1 for r in rows if r["channel"] == "mic"),
            "sys_segments": sum(1 for r in rows if r["channel"] == "sys"),
            "mic_langs": {}, "sys_langs": {}}
    stamp = d.name[:15]
    (d / f"{stamp}-transcript.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    (d / f"{stamp}-transcript.md").write_text("# t\n", encoding="utf-8")
    (d / f"{stamp}-transcript.raw.md").write_text("ESKİ HAM METİN\n", encoding="utf-8")
    (d / f"{stamp}-action-items.md").write_text("# rapor\n", encoding="utf-8")


def main() -> int:
    if not Path(T.FFMPEG).exists():
        print(f"  ! ffmpeg bulunamadı ({T.FFMPEG}) — ses testi atlanıyor")
        return 0
    tmp = Path(tempfile.mkdtemp())
    ok = True
    checks = []
    try:
        # --- kuru çalıştırma hiçbir şeye dokunmamalı ------------------------
        d0 = tmp / "2026-10-20-1619-auto"
        make_meeting(d0, 1200.0)
        before = dur(d0 / "sys.wav")
        T.trim(d0, 0.0, 600.0, dry=True)
        checks.append(("--dry-run sese dokunmadı", dur(d0 / "sys.wav") == before))
        checks.append(("--dry-run ham metni silmedi",
                       (d0 / "2026-10-20-1619-transcript.raw.md").exists()))

        # --- gerçek kırpma: 20 dk kayıttan ilk 10 dakika --------------------
        T.trim(d0, 0.0, 600.0)
        checks += [
            (f"mic.wav {dur(d0 / 'mic.wav'):.0f} sn = 600", abs(dur(d0 / "mic.wav") - 600) < 2),
            (f"sys.wav {dur(d0 / 'sys.wav'):.0f} sn = 600", abs(dur(d0 / "sys.wav") - 600) < 2),
            ("eski ham metin silindi",
             not (d0 / "2026-10-20-1619-transcript.raw.md").exists()),
            ("rapor .superseded.md oldu",
             (d0 / "2026-10-20-1619-action-items.superseded.md").exists()),
            ("rapor artık bekleyen sayılmıyor",
             find_output(d0, "action-items.md") is None),
        ]
        js = json.loads(find_output(d0, "transcript.json").read_text(encoding="utf-8"))
        rows, meta = js["rows"], js["meta"]
        checks += [
            (f"satır sayısı yarıya indi ({len(rows)} = 10)", len(rows) == 10),
            (f"meta süresi {meta['duration']:.0f} ≤ 600", meta["duration"] <= 600),
            ("meta segment sayıları satırlardan yeniden hesaplandı",
             meta["mic_segments"] + meta["sys_segments"] == len(rows)),
            ("kırpma kaydı meta'ya yazıldı", meta.get("trimmed", {}).get("to_s") == 600.0),
            ("bölme parçası olmayan klasöre split_from yazılmadı",
             "split_from" not in meta),
        ]

        # --- baştan kırpma: zamanlar kaymalı --------------------------------
        d1 = tmp / "2026-10-19-1416-auto"
        make_meeting(d1, 600.0)
        T.trim(d1, 120.0, None)
        js = json.loads(find_output(d1, "transcript.json").read_text(encoding="utf-8"))
        first = min(float(r["start"]) for r in js["rows"])
        checks += [
            (f"baştan 2 dk atıldı, ses {dur(d1 / 'sys.wav'):.0f} sn = 480",
             abs(dur(d1 / "sys.wav") - 480) < 2),
            (f"ilk satır 0'a yakın ({first:.0f} sn)", first < 30),
            ("hiçbir satır negatif zamana düşmedi",
             all(float(r["start"]) >= 0 for r in js["rows"])),
        ]

        # --- hata durumları --------------------------------------------------
        d2 = tmp / "2026-10-18-0900-auto"
        make_meeting(d2, 300.0)
        for label, args in (("kayıt dışı --to reddediliyor", (0.0, 9999.0)),
                            ("ters pencere reddediliyor", (200.0, 100.0)),
                            ("boş çağrı reddediliyor", (0.0, None))):
            try:
                T.trim(d2, *args)
                checks.append((label, False))
            except SystemExit:
                checks.append((label, True))
        checks.append(("reddedilen çağrılar sese dokunmadı",
                       abs(dur(d2 / "sys.wav") - 300) < 2))

        for label, good in checks:
            ok &= good
            print(f"  {'✓' if good else '✗'}  {label}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
