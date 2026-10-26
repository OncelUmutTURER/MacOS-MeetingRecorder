"""Tek kayda düşmüş iki toplantıyı transkript seviyesinde ayırır.

NEDEN VAR: arka arkaya toplantıya girildiğinde otomatik kayıt ikisini tek dosyada
birleştiriyor. Kullanıcı bunu elle ikiye bölüyordu ama `transcript.json` içindeki zaman
damgaları, segment sayıları ve tr/en dağılımı elle bölünce bozuluyordu.

NEDEN YENİDEN TRANSKRİPT GEREKMİYOR: `transcript.json` her satırda `start`, `end`,
`lang`, `speaker`, `channel` tutuyor. Yani meta'nın tamamı (süre, mic/sys segment
sayısı, baskın dil, dil dağılımı, konuşmacı listesi) satırlardan **birebir** yeniden
hesaplanabilir. Bölme anında ve kayıpsız.

SES DOSYALARINA DOKUNULMAZ (kullanıcı tercihi). mic.wav / sys.wav orijinal klasörde
kalır; yarımlar yalnızca transkript + json + kaynak notu içerir. Orijinal klasöre
`.skip` konur ki rapor bekleyenler listesinde iki kez görünmesin.

KONUŞMACI ETİKETLERİ DEĞİŞTİRİLMEZ. Aynı etiket aynı ses demek; yeniden numaralamak
iki yarı arasındaki eşleşmeyi bozar. İkinci yarı SPEAKER_00 yerine SPEAKER_02'den
başlayabilir, bu normaldir.

Kullanım:
    splitmeeting.py <klasör> --suggest              bölme noktası adaylarını listele
    splitmeeting.py <klasör> --at 01:12:30          o noktadan böl
    splitmeeting.py <klasör> --at 4350              saniye olarak da olur
    splitmeeting.py <klasör> --at ... --dry-run     ne olacağını göster, yazma
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import subprocess  # noqa: E402

from common import (FFMPEG, STAMP_RE, fmt_ts, find_output,  # noqa: E402
                    load_config, resolve_session, session_stamp)

FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")


def parse_time(s: str) -> float:
    """'01:12:30', '72:30' ya da '4350' kabul eder."""
    s = s.strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = [float(p) for p in s.split(":")]
    if not 2 <= len(parts) <= 3:
        raise SystemExit(f"HATA: zaman anlaşılmadı: {s!r} (örn: 01:12:30 ya da 4350)")
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def load_rows(session: Path) -> tuple[dict, list[dict]]:
    p = find_output(session, "transcript.json")
    if p is None:
        raise SystemExit(f"HATA: {session} içinde transcript.json yok.")
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = sorted(d["rows"], key=lambda r: float(r["start"]))
    return d.get("meta", {}), rows


def suggest(rows: list[dict], want: int = 8, min_gap: float = 45.0) -> list[tuple[float, float]]:
    """Konuşmanın tamamen durduğu boşlukları bulur.

    İki toplantı arasında genelde her iki kanal da uzun süre sessiz kalır. Boşluğun
    ORTASI bölme noktası olarak önerilir — böylece iki yarının da başı/sonu kırpılmaz.
    """
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for r in rows:
        st, en = float(r["start"]), float(r["end"])
        if st - cursor >= min_gap:
            gaps.append((cursor, st))
        cursor = max(cursor, en)
    gaps.sort(key=lambda g: -(g[1] - g[0]))
    return gaps[:want]


def _meta_for(rows: list[dict], base: dict, title: str,
              source: str, at: float) -> dict:
    mic = [r for r in rows if r.get("channel") == "mic"]
    sysr = [r for r in rows if r.get("channel") == "sys"]

    def dist(rs):
        return dict(Counter(r.get("lang") for r in rs if r.get("lang")))

    def top(d):
        return max(d, key=d.get) if d else None

    ml, sl = dist(mic), dist(sysr)
    return {
        "title": title,
        "duration": max((float(r["end"]) for r in rows), default=0.0),
        "mic_segments": len(mic),
        "sys_segments": len(sysr),
        "mic_lang": top(ml),
        "sys_lang": top(sl),
        "mic_langs": ml,
        "sys_langs": sl,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "speakers": sorted({r["speaker"] for r in sysr if r.get("speaker")}),
        "diar_device": base.get("diar_device"),
        "diar_fellback": base.get("diar_fellback"),
        # bleed_dropped TAŞINMAZ: birleştirme adımında tüm kayıt için hesaplanmış tek
        # sayı, yarılara bölünemez. Yanlış sayı yazmaktansa hiç yazma.
        "split_from": source,
        "split_at_s": round(at, 2),
    }


def _wav_duration(p: Path) -> float:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def split_audio(session: Path, out1: Path, out2: Path, at: float) -> list[str]:
    """mic.wav ve sys.wav'ı bölme noktasından ikiye keser.

    Kopyalamıyoruz, KESİYORUZ: iki yarının toplam boyutu orijinalle aynı.
    16 kHz mono PCM olduğu için `-c copy` örnek hizasında temiz kesiyor, yeniden
    kodlama yok — bir saatlik kayıt saniyeler içinde bölünüyor.

    `at` TRANSKRİPT saatidir (duvar saati). Ses dosyası ondan kısadır — yakalama
    duvar saatinin gerisinde kalıyor (bak: timeline.py) — bu yüzden her kanal
    kendi haritasıyla dosya zamanına çevrilerek kesilir. Harita yoksa aynen
    kullanılır, eski kayıtlar bozulmaz.

    Dönen: uyarı satırları (boş liste = sorunsuz).
    """
    import timeline as TL
    warns: list[str] = []
    for name in ("mic.wav", "sys.wav"):
        src = session / name
        if not src.exists():
            warns.append(f"{name} yok, atlandı")
            continue
        at_file = TL.audio_time(session, at, "mic" if name.startswith("mic") else "sys")
        if abs(at_file - at) > 1.0:
            print(f"     {name}: kesim {fmt_ts(at)} (transkript) → "
                  f"{fmt_ts(at_file)} (dosya içi)")
        total = _wav_duration(src)
        # SIRA ÖNEMLİ: ekleme modunda ön parça kaynağın ta kendisi. Önce ARKA parçayı
        # kes (kaynak hâlâ tam), sonra öndekini yerinde kırp. Ters sırada arka parça
        # kırpılmış dosyadan okunur ve boş çıkar.
        plan = [(out1, None, at_file), (out2, at_file, None)]
        if (out1 / name).resolve() == src.resolve():
            plan.reverse()
        for out, ss, to in plan:
            dest = out / name
            # ffmpeg aynı dosyaya hem okuyup hem yazamaz. Ekleme modunda ön parça
            # kendi klasöründe kırpıldığı için geçici dosyaya yazıp yer değiştiriyoruz.
            inplace = dest.resolve() == src.resolve()
            target = dest.with_suffix(".cut.tmp.wav") if inplace else dest
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
            if ss is not None:
                cmd += ["-ss", f"{ss:.3f}"]
            cmd += ["-i", str(src)]
            if to is not None:
                cmd += ["-t", f"{to:.3f}"]
            cmd += ["-c", "copy", str(target)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                warns.append(f"{out.name}/{name}: ffmpeg hatası — "
                             f"{(r.stderr or '').strip()[:120]}")
                target.unlink(missing_ok=True)
                continue
            if inplace:
                target.replace(dest)
        # mutabakat: iki parçanın toplamı orijinali tutmalı
        d1, d2 = _wav_duration(out1 / name), _wav_duration(out2 / name)
        if total and abs((d1 + d2) - total) > 2.0:
            warns.append(f"{name}: {d1:.0f} + {d2:.0f} = {d1 + d2:.0f} sn, "
                         f"orijinal {total:.0f} sn — FARK VAR")
        else:
            print(f"     {name}: {fmt_ts(d1)} + {fmt_ts(d2)} = {fmt_ts(d1 + d2)} "
                  f"(orijinal {fmt_ts(total)}) ✓")
    return warns


PART_RE = re.compile(r"-part(\d+)$")


def part_info(session: Path) -> tuple[str, float] | None:
    """Bu klasör bir bölme parçası mı? (kaynak toplantının adı, ses içindeki konumu).

    Parça olup olmadığını ADA bakarak değil `meta.split_from` alanına bakarak anlıyoruz;
    kullanıcı klasörü yeniden adlandırmış olabilir.
    """
    p = find_output(session, "transcript.json")
    if not p:
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8")).get("meta", {})
    except (OSError, json.JSONDecodeError):
        return None
    origin = meta.get("split_from")
    if not origin:
        return None
    return origin, float(meta.get("audio_offset_s") or 0.0)


def series_of(parent: Path, origin: str) -> list[tuple[int, float, Path]]:
    """Aynı kaynaktan gelen tüm parçalar, ses içindeki sıralarına göre."""
    out: list[tuple[int, float, Path]] = []
    for d in parent.iterdir():
        if not d.is_dir():
            continue
        info = part_info(d)
        if info and info[0] == origin:
            m = PART_RE.search(d.name)
            out.append((int(m.group(1)) if m else 0, info[1], d))
    out.sort(key=lambda x: x[1])
    return out


def _renumber_after(series: list[tuple[int, float, Path]], after_offset: float) -> list[str]:
    """Verilen konumdan SONRAKİ parçaların numarasını bir artırır.

    Araya yeni bir parça girdiğinde seri bozulmasın diye. Yüksekten alçağa gidiyoruz,
    yoksa part3 → part4 yaparken mevcut part4 ile çakışırız.
    """
    moved = []
    for num, off, d in sorted(series, key=lambda x: -x[1]):
        if off <= after_offset:
            continue
        m = PART_RE.search(d.name)
        if not m:
            continue
        new = d.parent / (d.name[:m.start()] + f"-part{int(m.group(1)) + 1}")
        if new.exists():
            raise SystemExit(f"HATA: {new.name} zaten var, numaralandırma yapılamıyor.")
        d.rename(new)
        moved.append(f"{d.name} → {new.name}")
    return moved


def split(session: Path, at: float, dry: bool = False,
          drop_audio: bool = False) -> int:
    cfg = load_config()
    base, rows = load_rows(session)
    total = max((float(r["end"]) for r in rows), default=0.0)
    if not 0 < at < total:
        raise SystemExit(f"HATA: bölme noktası kaydın içinde olmalı "
                         f"(0 – {fmt_ts(total)}), verilen: {fmt_ts(at)}")

    first = [r for r in rows if float(r["start"]) < at]
    tail = [r for r in rows if float(r["start"]) >= at]

    if not first or not tail:
        raise SystemExit("HATA: bölme noktası bir tarafı boş bırakıyor.")

    # İkinci yarı TAM OLARAK verilen noktaya göre sıfırlanır.
    #
    # Bir ara "kendi ilk konuşmasına göre hizala" denendi ve YANLIŞTI: kullanıcı yeni
    # toplantıya geçtiğinde oradaki konuyu bölmemek için bir süre susuyor. O sessizlik
    # toplantının parçası; onu atlamak bölme noktasını kullanıcının verdiği yerden
    # kaydırıyor ve klasör damgası da yanlış saati gösteriyordu.
    # Verilen zaman neyse orası bölme noktasıdır.
    origin = at
    second = []
    for r in tail:
        r = dict(r)
        r["start"] = round(float(r["start"]) - origin, 3)
        r["end"] = round(float(r["end"]) - origin, 3)
        second.append(r)
    first_voice = min(float(r["start"]) for r in tail) - at

    stamp = session_stamp(session)
    m = STAMP_RE.match(session.name)
    if not m:
        raise SystemExit(f"HATA: klasör adı tarih damgası taşımıyor: {session.name}")
    t0 = dt.datetime.strptime(stamp, "%Y-%m-%d-%H%M")
    stamp2 = (t0 + dt.timedelta(seconds=origin)).strftime("%Y-%m-%d-%H%M")

    # ZATEN BİR PARÇAYI mı bölüyoruz? Öyleyse seriye EKLEME yapılır, yeni bir seri
    # başlatılmaz. Kullanıcı bunu bildirdi: M1 → part1 + part2 iyiydi, ama part2'yi
    # tekrar bölünce ikinci bir "part1/part2" çifti oluşuyor, numaraları elle
    # düzeltip fazla klasörleri silmek gerekiyordu.
    #
    # Ekleme modunda: ön parça BULUNDUĞU KLASÖRDE kalır (yerinde kırpılır), arka parça
    # bir sonraki numarayı alır, ondan sonraki parçalar bir kayar. Ham kayıt zaten
    # kaynak toplantıda duruyor; yeni bir ham kopya üretilmez.
    part = part_info(session)
    series = series_of(session.parent, part[0]) if part else []
    my_num = 0
    if part:
        my_num = next((n for n, off, d in series if d == session), 0)
        if not my_num:
            raise SystemExit(f"HATA: {session.name} bir parça gibi görünüyor ama seride "
                             f"numarası bulunamadı. Klasör adı '-partN' ile bitmeli.")
        origin_name = part[0]
        base_offset = part[1]
        out1 = session
        out2 = session.parent / f"{stamp2}-part{my_num + 1}"
    else:
        origin_name = session.name
        base_offset = 0.0
        out1 = session.parent / f"{stamp}-part1"
        out2 = session.parent / f"{stamp2}-part2"

    print(f"Kaynak : {session.name}  ({fmt_ts(total)}, {len(rows)} satır)")
    print(f"Bölme  : {fmt_ts(at)}")
    if part:
        later = [d.name for n, off, d in series if off > base_offset]
        print(f"Mod    : SERİYE EKLEME — kaynak '{origin_name}', bu parça #{my_num}")
        print(f"         ön parça {session.name} içinde KALIYOR, arka parça "
              f"part{my_num + 1} oluyor")
        if later:
            print(f"         sonraki parçalar bir kayacak: {', '.join(later)}")
    print()
    if first_voice > 1.0:
        print(f"         (2. yarıda ilk konuşma {fmt_ts(first_voice)} sonra başlıyor — "
              f"bu sessizlik korunuyor)")
    print()
    for label, out, rs in (("1. yarı", out1, first), ("2. yarı", out2, second)):
        meta = _meta_for(rs, base, out.name, session.name, at)
        mic, sysn = meta["mic_segments"], meta["sys_segments"]
        print(f"  {label}: {out.name}")
        print(f"     süre {fmt_ts(meta['duration'])} · {len(rs)} satır "
              f"(mic {mic} / sys {sysn})")
        print(f"     dil  mic {meta['mic_langs']} · sys {meta['sys_langs']}")
        print(f"     konuşmacılar: {', '.join(meta['speakers']) or '—'}")
        # Ekleme modunda ön parça zaten var ve YERİNDE güncellenecek; o beklenen durum.
        if out.exists() and not dry and out != session:
            raise SystemExit(f"HATA: {out} zaten var, üzerine yazmıyorum.")

    # MUTABAKAT: yarıların toplamı orijinalin meta'sını tutmalı. Tutmuyorsa satır
    # kaybı ya da hatalı sınıflandırma var demektir — sessizce devam etme.
    print()
    m1 = _meta_for(first, base, "", session.name, at)
    m2 = _meta_for(second, base, "", session.name, at)
    problems = []
    for key in ("mic_segments", "sys_segments"):
        got, want = m1[key] + m2[key], base.get(key)
        mark = "✓" if want is None or got == want else "✗"
        if mark == "✗":
            problems.append(f"{key}: {got} ≠ {want}")
        print(f"  {mark} {key:<14} {m1[key]} + {m2[key]} = {got}"
              + (f"   (orijinal {want})" if want is not None else ""))
    for key in ("mic_langs", "sys_langs"):
        got = Counter(m1[key]) + Counter(m2[key])
        want = base.get(key) or {}
        mark = "✓" if not want or dict(got) == dict(want) else "✗"
        if mark == "✗":
            problems.append(f"{key}: {dict(got)} ≠ {want}")
        print(f"  {mark} {key:<14} {dict(got)}"
              + (f"   (orijinal {want})" if want else ""))
    lost = len(rows) - (len(first) + len(second))
    print(f"  {'✓' if lost == 0 else '✗'} satır           "
          f"{len(first)} + {len(second)} = {len(first) + len(second)}"
          f"   (orijinal {len(rows)})")
    if lost:
        problems.append(f"{lost} satır kayboldu")
    if problems:
        raise SystemExit("\nHATA: mutabakat tutmadı — " + " · ".join(problems))

    if dry:
        print("\n(KURU ÇALIŞTIRMA — hiçbir dosya yazılmadı, ses bölünmedi)")
        print("Bölme noktası doğru görünüyorsa aynı komutu --dry-run olmadan çalıştır.")
        return 0

    # Numaralandırma ÖNCE: arka parça yazılmadan sonraki parçalar kaydırılmalı,
    # yoksa part3 adını iki klasör birden ister.
    if part:
        for line in _renumber_after(series, base_offset):
            print(f"  ↷ {line}")

    import pipeline
    for out, rs, off in ((out1, first, base_offset), (out2, second, base_offset + at)):
        out.mkdir(parents=True, exist_ok=True)
        # Yerinde güncellenen ön parçanın ESKİ türevleri silinmeli. Özellikle
        # `*-transcript.raw.md`: fixterms düzeltmeleri ham hâlden yeniden uyguluyor,
        # eski ham dosya kalırsa kırpılmış metnin üstüne eski uzun metni geri yazar.
        if out == session:
            for pat in ("*-transcript.raw.md", "*-corrections.md"):
                for f in out.glob(pat):
                    f.unlink()
            old = find_output(out, "action-items.md")
            if old:
                old.rename(old.with_name(old.stem + ".superseded.md"))
                print(f"  ! {out.name}: raporu artık eski kapsamı anlatıyor, "
                      f"kenara alındı")
        meta = _meta_for(rs, base, out.name, origin_name, at)
        meta["audio_offset_s"] = round(off, 2)
        pipeline.write_transcript(out, rs, meta, cfg)
        (out / "SOURCE.md").write_text(
            f"# Kaynak\n\nBu klasör `{origin_name}` kaydının parçası.\n"
            + (f"Son bölme: `{session.name}` içinde **{fmt_ts(at)}** noktasından.\n\n"
               if part else f"Bölme noktası: **{fmt_ts(at)}**.\n\n")
            + f"- Kendi `mic.wav` / `sys.wav` dosyaları var; kopya değil, kaynaktan "
            f"kesildi. Parçaların toplam boyutu kaynakla aynıdır.\n"
            f"- Kaynak kayıttaki konumu: **{fmt_ts(off)}**\n"
            f"- Zaman damgaları bölme noktasına göre sıfırlandı. Toplantının başındaki "
            f"sessizlik varsa korunur — bölme tam verilen saatte yapılır.\n"
            f"- Konuşmacı etiketleri değiştirilmedi: aynı etiket = aynı ses. "
            f"İkinci yarı SPEAKER_00'dan başlamayabilir.\n"
            f"- `bleed_dropped` taşınmadı — tüm kayıt için hesaplanmış tek sayıydı.\n"
            f"- Ham kayıt ve loglar kaynak klasörde: `{session.parent / origin_name}`\n",
            encoding="utf-8")
        print(f"  ✓ {out.name} yazıldı")

    # Orijinalin raporu varsa artık YANLIŞ kapsamı anlatıyor: tek toplantı sanıp iki
    # toplantıyı birlikte özetlemiş. Yarımlar zaten raporsuz doğuyor, yani `meet pending`
    # ve zamanlanmış görev ikisini de yeniden işleyecek. Eski raporu silmiyoruz
    # (kullanıcının verisi) ama adını değiştirip listeden ve karışıklıktan çıkarıyoruz.
    # Ekleme modunda bu zaten yukarıdaki döngüde yapıldı (ön parça = session).
    old_report = None if part else find_output(session, "action-items.md")
    if old_report:
        stale = old_report.with_name(old_report.stem + ".superseded.md")
        old_report.rename(stale)
        print(f"\n  ! orijinalin raporu iki toplantıyı birden anlatıyordu — "
              f"{stale.name} olarak kenara alındı")
        print("    iki yarı için yeni rapor üretilecek (rapor bekleyenler listesinde)")

    print("\n  ses bölünüyor:")
    warns = split_audio(session, out1, out2, at)
    for w in warns:
        print(f"     ⚠ {w}")

    if part:
        # Ekleme modunda session artık gerçek bir parça — `.skip` KONMAZ, rapor
        # bekleyenler listesinde kalmalı. Ham kayıt zaten kaynak klasörde duruyor,
        # yeni bir yedek üretilmedi.
        final = series_of(session.parent, origin_name)
        print(f"\nSeri güncel hâli ({origin_name}):")
        for n, off, d in final:
            print(f"  part{n:<2} {fmt_ts(off):>9}  {d.name}")
        print("\nHam kayıt kaynak klasörde; yeni yedek üretilmedi.")
        return 0

    # Orijinal artık rapor beklemesin, yoksa aynı içerik üç kez listelenir.
    (session / ".skip").write_text(
        f"Bu kayıt {fmt_ts(at)} noktasından ikiye bölündü:\n"
        f"  {out1.name}\n  {out2.name}\n"
        f"Yarımların kendi ses dosyaları var. Buradaki mic.wav/sys.wav yedek olarak\n"
        f"duruyor; yer açmak için: meet split \"{session}\" --drop-original-audio\n",
        encoding="utf-8")
    print(f"  ✓ {session.name} rapor listesinden çıkarıldı (.skip)")

    if drop_audio:
        if warns:
            print("\n  ⚠ mutabakat uyarısı var — orijinal ses SİLİNMEDİ.")
        else:
            freed = 0
            for name in ("mic.wav", "sys.wav"):
                p = session / name
                if p.exists():
                    freed += p.stat().st_size
                    p.unlink()
            print(f"  ✓ orijinal ses silindi, {freed / 1e6:.0f} MB açıldı")
    else:
        size = sum((session / n).stat().st_size
                   for n in ("mic.wav", "sys.wav") if (session / n).exists())
        print(f"\nOrijinal ses yedek olarak duruyor ({size / 1e6:.0f} MB).")
        print(f"Yer açmak istersen:  meet split \"{session}\" --drop-original-audio")
    return 0


def drop_original_audio(session: Path) -> int:
    """Bölünmüş bir kaydın orijinal ses dosyalarını siler (yarımlar doğrulandıktan sonra)."""
    skip = session / ".skip"
    if not skip.exists() or "bölündü" not in skip.read_text(encoding="utf-8"):
        raise SystemExit(f"HATA: {session.name} bölünmüş görünmüyor. "
                         f"Önce `meet split ... --at <zaman>` çalıştır.")
    halves = [session.parent / l.strip() for l in skip.read_text(encoding="utf-8").splitlines()
              if l.startswith("  ") and (session.parent / l.strip()).is_dir()]
    if len(halves) != 2:
        raise SystemExit("HATA: iki yarı bulunamadı, silmiyorum.")

    freed = 0
    for name in ("mic.wav", "sys.wav"):
        src = session / name
        if not src.exists():
            continue
        total = _wav_duration(src)
        parts = sum(_wav_duration(h / name) for h in halves if (h / name).exists())
        if not total or abs(parts - total) > 2.0:
            raise SystemExit(f"HATA: {name} mutabakatı tutmuyor "
                             f"({parts:.0f} ≠ {total:.0f} sn) — silmiyorum.")
        print(f"  ✓ {name}: yarımlar {fmt_ts(parts)} = orijinal {fmt_ts(total)}")
        freed += src.stat().st_size
        src.unlink()
    print(f"\n{freed / 1e6:.0f} MB açıldı. Loglar ve .skip yerinde duruyor.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Birleşmiş toplantıyı ikiye ayırır",
        epilog="Klasör verilmezse bulunduğun dizin kullanılır — toplantı klasörünün "
               "içindeysen sadece `meet split --at 01:12:30` yeter.")
    ap.add_argument("session", nargs="?",
                    help="toplantı klasörü (boşsa bulunduğun dizin)")
    ap.add_argument("--at", help="bölme noktası: 01:12:30 ya da saniye")
    ap.add_argument("--suggest", action="store_true", help="aday noktaları listele")
    ap.add_argument("--min-gap", type=float, default=45.0,
                    help="aday için en az sessizlik (sn, varsayılan 45)")
    ap.add_argument("--dry-run", action="store_true",
                    help="hiçbir şey yazma: iki yarının süresi, satır/dil dağılımı ve "
                         "mutabakatı göster, bölme noktasını onaylaman için")
    ap.add_argument("--drop-original-audio", action="store_true",
                    help="bölme sonrası orijinal mic.wav/sys.wav'ı sil (yer açar)")
    a = ap.parse_args()

    session = resolve_session(a.session)

    if a.drop_original_audio and not a.at:
        raise SystemExit(drop_original_audio(session))

    if a.suggest or not a.at:
        _, rows = load_rows(session)
        total = max((float(r["end"]) for r in rows), default=0.0)
        gaps = suggest(rows, min_gap=a.min_gap)
        print(f"{session.name} — {fmt_ts(total)}, {len(rows)} satır")
        print(f"\n{a.min_gap:.0f} saniyeden uzun sessizlikler (uzundan kısaya):\n")
        if not gaps:
            print("  Aday bulunamadı. --min-gap değerini düşürmeyi dene "
                  "(örn: --min-gap 20).")
        for s, e in gaps:
            mid = (s + e) / 2
            print(f"  {fmt_ts(s)} → {fmt_ts(e)}   ({e - s:5.0f} sn boşluk)   "
                  f"öneri: --at {fmt_ts(mid)}")
        print("\nSeçtikten sonra:  meet split \"<klasör>\" --at <zaman> --dry-run")
        return

    split(session, parse_time(a.at), a.dry_run, a.drop_original_audio)


if __name__ == "__main__":
    main()
