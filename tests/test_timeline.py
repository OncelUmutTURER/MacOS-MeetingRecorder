"""Dosya zamanı ↔ duvar saati eşlemesi.

ÖLÇÜLEN SORUN (2026-10-21, 119 kayıt): avfoundation bildirdiği hızda örnek
üretmiyor; yakalanan dosya gerçek geçen süreden %5-18 kısa çıkıyor ve iki kanal
farklı oranlarda kaydığı için birleşik transkriptte konuşmalar yanlış sıraya
giriyor. Düzeltme sese dokunmadan, ffmpeg ilerleme logundaki
`size=` (yazılan bayt) ve `time=` (gerçek geçen süre) çiftlerinden yapılıyor.

Burada sınananlar:
  · log ayrıştırma ve harita kurma
  · dosya→duvar ve ters yön (ikisi birbirini tutmalı)
  · düzeltmenin GEREKMEDİĞİ durumda hiçbir şeyi bozmaması
  · harita yokken eski kayıtların aynen çalışması
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import timeline as TL  # noqa: E402

SR = 16000


def fake_log(path: Path, wall_total: float, loss: float, epoch: float | None = None,
             step: float = 0.5) -> None:
    """Kayıp oranı `loss` olan bir ffmpeg ilerleme logu üretir."""
    lines = []
    if epoch is not None:
        lines.append(f"  Stream #0:0: Audio: pcm_f32le, 48000 Hz, mono, start: {epoch:.6f}\n")
    t = step
    while t <= wall_total + 1e-9:
        file_s = t * (1 - loss)
        kib = int((file_s * SR * 2 + 44) / 1024)
        h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
        lines.append(f"size= {kib:8}KiB time={h:02}:{m:02}:{s:05.2f} "
                     f"bitrate= 256.0kbits/s speed=   1x elapsed={h}:{m:02}:{s:05.2f}    \r")
        t += step
    path.write_text("".join(lines), encoding="utf-8")


def check(label: str, got, want, tol: float = 0.0) -> bool:
    ok = abs(got - want) <= tol if isinstance(want, (int, float)) else got == want
    print(f"  {'✓' if ok else '✗'}  {label}"
          + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def main() -> int:
    ok = True
    tmp = Path(tempfile.mkdtemp())
    cfg = {"audio": {"work_sample_rate": SR}}

    print("log ayrıştırma:")
    log = tmp / "sys.ffmpeg.log"
    fake_log(log, 600.0, 0.07, epoch=1000.0)       # 10 dk, %7 kayıp
    pairs = TL.log_pairs(log, SR)
    ok &= check(f"{len(pairs)} çift okundu", len(pairs) > 1000, True)
    ok &= check("son duvar saati 600 sn", pairs[-1][1], 600.0, 0.6)
    ok &= check("son dosya konumu 558 sn (%7 kayıp)", pairs[-1][0], 558.0, 1.0)

    print("\ndosya → duvar dönüşümü:")
    m = [[f, w] for f, w in TL._thin(pairs)]
    ok &= check("başlangıç kaymıyor", TL.to_wall(m, 0.0), 0.0, 0.1)
    ok &= check("279. saniye (dosya) = 300. saniye (duvar)", TL.to_wall(m, 279.0), 300.0, 1.0)
    ok &= check("ters yön: 300 duvar = 279 dosya", TL.to_file(m, 300.0), 279.0, 1.0)
    for t in (50.0, 123.4, 420.0, 557.0):
        ok &= check(f"gidiş-dönüş {t} sn", TL.to_file(m, TL.to_wall(m, t)), t, 0.2)
    ok &= check("harita dışına taşan değer kırpılmıyor, ötelenmiş dönüyor",
                TL.to_wall(m, 600.0) > 600.0, True)

    print("\niki kanal birlikte (asıl mesele):")
    s = tmp / "oturum"
    s.mkdir()
    fake_log(s / "sys.ffmpeg.log", 1800.0, 0.06, epoch=1000.0)      # %6
    fake_log(s / "mic.part01.log", 1800.0, 0.09, epoch=1000.05)     # %9
    maps = TL.build(s, cfg)
    ok &= check("iki kanal için de harita kuruldu", sorted(maps), ["mic", "sys"])
    # Düzeltme ÖNCESİ: aynı anda söylenen iki cümle dosyada farklı yerde duruyor
    wall = 1500.0
    f_sys, f_mic = wall * 0.94, wall * 0.91
    gap_before = abs(f_sys - f_mic)
    gap_after = abs(TL.to_wall(maps["sys"], f_sys) - TL.to_wall(maps["mic"], f_mic))
    print(f"     25. dakikada kanallar arası fark: {gap_before:.0f} sn → {gap_after:.1f} sn")
    ok &= check("düzeltmeden önce fark 40+ sn", gap_before > 40, True)
    ok &= check("düzeltmeden sonra fark 1 sn altı", gap_after < 1.0, True)

    print("\nsegmentlere uygulama:")
    segs = [{"start": 100.0, "end": 110.0, "text": "a"},
            {"start": 940.0, "end": 950.0, "text": "b"}]
    out = TL.apply(maps["sys"], segs)
    ok &= check("metin korunuyor", out[1]["text"], "b")
    ok &= check("sonraki segment daha çok ötelendi",
                out[1]["start"] - 940.0 > out[0]["start"] - 100.0, True)
    ok &= check("sıra bozulmuyor", out[0]["start"] < out[1]["start"], True)

    print("\nkayıt/okuma ve geri uyumluluk:")
    TL.save(s, cfg)
    ok &= check("timeline.json yazıldı", (s / TL.NAME).exists(), True)
    d = TL.load(s)
    ok &= check("sapma kaydedildi", d["drift_s"]["sys"] > 90, True)
    ok &= check("audio_time duvar→dosya çeviriyor",
                TL.audio_time(s, 1000.0, "sys"), 940.0, 2.0)
    ok &= check("wall_time dosya→duvar çeviriyor",
                TL.wall_time(s, 940.0, "sys"), 1000.0, 2.0)
    bos = tmp / "haritasiz"
    bos.mkdir()
    ok &= check("HARİTA YOKSA saat aynen döner (eski kayıtlar bozulmaz)",
                TL.audio_time(bos, 777.0), 777.0)
    ok &= check("harita yoksa build boş döner", TL.build(bos, cfg), {})

    print("\nkayıpsız kayıt (düzeltme gerekmeyen hâl):")
    t2 = tmp / "temiz"
    t2.mkdir()
    fake_log(t2 / "sys.ffmpeg.log", 600.0, 0.0, epoch=1000.0)
    mm = TL.build(t2, cfg)["sys"]
    ok &= check("kayıpsız kayıtta saat değişmiyor", TL.to_wall(mm, 300.0), 300.0, 0.6)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
