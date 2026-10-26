"""Bir kaydın başından ve/veya sonundan fazlalığı kırpar.

NEDEN VAR: otomatik kayıt bazen toplantı bittikten sonra da sürüyordu (kulaklık
kutuya girince izlenen çıkış cihazı değişiyor ve bir daha boşa düşmüyor —
ölçüldü, 2026-10-21). Kullanıcı bu kuyrukları `mic.wav`/`sys.wav` üzerinde ELLE
kırpmak zorunda kalıyordu; elle kırpınca da transkript ve meta tutarsızlaşıyor.

`meet trim` aynı işi kayıpsız ve tutarlı yapar:

  - ses `-c copy` ile kesilir (yeniden kodlama yok, saniyeler sürer)
  - transkript varsa pencere dışındaki satırlar düşer, zamanlar kaydırılır,
    meta (süre, segment sayıları, dil dağılımı, konuşmacılar) satırlardan
    YENİDEN hesaplanır — `splitmeeting` ile aynı yöntem
  - `*-transcript.raw.md` ve `*-corrections.md` silinir: `fixterms` düzeltmeleri
    ham hâlden yeniden uyguluyor, eski ham dosya kalırsa kırpılmış metnin üstüne
    eski uzun metni geri yazar
  - varsa action item raporu `.superseded.md` olur: artık kırpılmış olmayan
    kapsamı anlatıyor

Çoğu zaman transkript HENÜZ YOKTUR (kuyruk fark edilince hemen kırpılır); o
durumda sadece ses kesilir ve transkript sonradan doğru süreyle çıkar.

Kullanım:
    trimmeeting.py [klasör] --to 01:05:00        sonrasını at
    trimmeeting.py [klasör] --from 00:02:30      öncesini at
    trimmeeting.py [klasör] --to 3900 --dry-run  ne olacağını göster, yazma

Klasör verilmezse bulunduğun dizin kullanılır.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import FFMPEG, find_output, fmt_ts, load_config  # noqa: E402
from common import resolve_session  # noqa: E402
from splitmeeting import (_meta_for, _wav_duration, load_rows,  # noqa: E402
                          parse_time)

WAVS = ("mic.wav", "sys.wav")


def audio_bounds(session: Path) -> dict[str, float]:
    return {n: _wav_duration(session / n) for n in WAVS if (session / n).exists()}


def cut_audio(session: Path, start: float, end: float | None) -> list[str]:
    """Ses dosyalarını [start, end] penceresine kırpar. Dönen: uyarılar.

    ffmpeg aynı dosyayı hem okuyup hem yazamaz — geçici dosyaya yazıp yer
    değiştiriyoruz (splitmeeting'deki ekleme modunun aynısı).
    """
    import timeline as TL
    warns: list[str] = []
    for name in WAVS:
        src = session / name
        if not src.exists():
            warns.append(f"{name} yok, atlandı")
            continue
        # Verilen saatler TRANSKRİPT saatidir; ses dosyası duvar saatinin
        # gerisinde kaldığı için her kanal kendi haritasıyla çevrilir (timeline.py).
        ch = "mic" if name.startswith("mic") else "sys"
        s_file = TL.audio_time(session, start, ch) if start else 0.0
        e_file = TL.audio_time(session, end, ch) if end is not None else None
        before = _wav_duration(src)
        tmp = src.with_suffix(".trim.tmp.wav")
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
        if s_file:
            cmd += ["-ss", f"{s_file:.3f}"]
        cmd += ["-i", str(src)]
        if e_file is not None:
            cmd += ["-t", f"{max(0.0, e_file - s_file):.3f}"]
        cmd += ["-c", "copy", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            warns.append(f"{name}: ffmpeg hatası — {(r.stderr or '').strip()[:120]}")
            tmp.unlink(missing_ok=True)
            continue
        got = _wav_duration(tmp)
        want = (e_file if e_file is not None else before) - s_file
        if want > 0 and abs(got - want) > 2.0:
            warns.append(f"{name}: {got:.0f} sn yazıldı, {want:.0f} sn bekleniyordu "
                         f"— ORİJİNAL KORUNDU")
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(src)
        print(f"     {name}: {fmt_ts(before)} → {fmt_ts(got)}")
    return warns


def trim(session: Path, start: float = 0.0, end: float | None = None,
         dry: bool = False) -> int:
    cfg = load_config()
    durs = audio_bounds(session)
    if not durs:
        raise SystemExit(f"HATA: {session} içinde mic.wav/sys.wav yok. "
                         f"Kayıt henüz birleştirilmemiş olabilir "
                         f"(mic.partNN.wav duruyorsa önce `meet stop` tamamlanmalı).")
    import timeline as TL
    total = max(durs.values())
    # Sınır kontrolü TRANSKRİPT ekseninde yapılmalı: zaman ekseni düzeltilmiş
    # kayıtlarda dosya, duvar saatinden kısadır ve geçerli bir `--to` değeri
    # dosya süresini aşabilir.
    total_wall = TL.wall_time(session, total)
    if end is not None and end > total_wall + 1:
        raise SystemExit(f"HATA: --to {fmt_ts(end)} kaydın dışında "
                         f"(kayıt {fmt_ts(total_wall)}).")
    if start >= (end if end is not None else total_wall):
        raise SystemExit("HATA: --from, --to'dan küçük olmalı.")
    if not start and end is None:
        raise SystemExit("HATA: --from ya da --to ver.")

    kept = (end if end is not None else total) - start
    print(f"Kayıt  : {session.name}")
    for n, d in durs.items():
        print(f"         {n:<8} {fmt_ts(d)}")
    print(f"Pencere: {fmt_ts(start)} → {fmt_ts(end) if end is not None else 'son'}"
          f"   ({fmt_ts(kept)} kalıyor, {fmt_ts(total - kept)} atılıyor)")

    # --- transkript varsa birlikte kırp ------------------------------------
    rows_out = None
    try:
        base, rows = load_rows(session)
    except SystemExit:
        base, rows = None, None
        print("\nTranskript yok — yalnızca ses kırpılacak "
              "(transkript sonradan doğru süreyle çıkar).")
    if rows is not None:
        hi = end if end is not None else float("inf")
        keep = [r for r in rows if float(r["start"]) >= start and float(r["start"]) < hi]
        rows_out = []
        for r in keep:
            r = dict(r)
            r["start"] = round(float(r["start"]) - start, 3)
            r["end"] = round(float(r["end"]) - start, 3)
            rows_out.append(r)
        meta = _meta_for(rows_out, base, session.name, base.get("split_from") or "", 0.0)
        print(f"\nTranskript: {len(rows)} satır → {len(rows_out)} satır "
              f"({len(rows) - len(rows_out)} düşüyor)")
        print(f"            mic {meta['mic_segments']} / sys {meta['sys_segments']} · "
              f"dil mic {meta['mic_langs']} sys {meta['sys_langs']}")
        if not rows_out:
            raise SystemExit("HATA: pencerede hiç satır kalmıyor — zamanları kontrol et.")

    if dry:
        print("\n(KURU ÇALIŞTIRMA — hiçbir dosya değişmedi)")
        return 0

    print("\n  ses kırpılıyor:")
    warns = cut_audio(session, start, end)
    for w in warns:
        print(f"     ⚠ {w}")
    if any("ORİJİNAL KORUNDU" in w or "ffmpeg hatası" in w for w in warns):
        raise SystemExit("\nHATA: ses kırpılamadı — transkripte dokunulmadı.")

    if rows_out is not None:
        import pipeline
        meta = _meta_for(rows_out, base, session.name, base.get("split_from") or "", 0.0)
        # split_from boşsa alanı hiç yazma: bu klasör bir bölme parçası değil.
        if not meta.get("split_from"):
            meta.pop("split_from", None)
            meta.pop("split_at_s", None)
        else:
            meta["audio_offset_s"] = base.get("audio_offset_s")
        meta["trimmed"] = {"from_s": round(start, 2),
                           "to_s": round(end, 2) if end is not None else None}
        # fixterms ham hâlden yeniden uyguluyor: eski ham dosya kalırsa kırpılmış
        # metnin üstüne eski uzun metni geri yazar.
        for pat in ("*-transcript.raw.md", "*-corrections.md"):
            for f in session.glob(pat):
                f.unlink()
        pipeline.write_transcript(session, rows_out, meta, cfg)
        print(f"  ✓ transkript yeniden yazıldı ({len(rows_out)} satır)")
        if (old := find_output(session, "action-items.md")):
            old.rename(old.with_name(old.stem + ".superseded.md"))
            print(f"  ! rapor kırpılmamış kaydı anlatıyordu — "
                  f"{old.stem}.superseded.md olarak kenara alındı")
            print("    yeni rapor bekleyenler listesinde üretilecek")

    print("\nBitti. Doğrulamak için:  meet inspect \"%s\"" % session)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Kaydın başından/sonundan fazlalığı kırpar (ses + transkript)",
        epilog="Klasör verilmezse bulunduğun dizin kullanılır.")
    ap.add_argument("session", nargs="?", help="toplantı klasörü")
    ap.add_argument("--from", dest="start", help="buradan öncesini at (01:02:30 ya da sn)")
    ap.add_argument("--to", dest="end", help="buradan sonrasını at")
    ap.add_argument("--dry-run", action="store_true",
                    help="hiçbir şey yazma, ne olacağını göster")
    a = ap.parse_args()
    session = resolve_session(a.session)
    trim(session,
         parse_time(a.start) if a.start else 0.0,
         parse_time(a.end) if a.end else None,
         a.dry_run)


if __name__ == "__main__":
    main()
