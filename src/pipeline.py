"""Toplantı sonrası işleme: transkript -> diarization -> birleştirme -> action item."""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import sys
from pathlib import Path

from common import (find_output, fmt_ts, load_config, load_env, meetings_dir,
                    output_path)


def latest_session(cfg: dict) -> Path:
    root = meetings_dir(cfg)
    dirs = [p for p in root.iterdir() if p.is_dir() and (p / "mic.wav").exists()] if root.exists() else []
    if not dirs:
        raise SystemExit(f"HATA: {root} altında işlenecek toplantı bulunamadı.")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def merge(mic_segs: list[dict], sys_segs: list[dict], me: str) -> list[dict]:
    rows = [{**s, "speaker": me, "channel": "mic"} for s in mic_segs]
    rows += [{**s, "channel": "sys"} for s in sys_segs]
    rows.sort(key=lambda r: (r["start"], 0 if r["channel"] == "mic" else 1))
    return rows


def drop_bleed(rows: list[dict], me: str, threshold: float = 0.80) -> tuple[list[dict], int]:
    """Kulaklıktan mikrofona sızan karşı taraf sesini ayıklar.

    Aynı anda hem mic hem sys kanalında neredeyse aynı metin varsa, mic'teki kopya
    (akustik sızıntı) atılır. Kulaklık kullanırken nadiren tetiklenir.
    """
    keep, dropped = [], 0
    for r in rows:
        if r["channel"] == "mic":
            leak = False
            for o in rows:
                if o["channel"] != "sys":
                    continue
                if min(r["end"], o["end"]) - max(r["start"], o["start"]) <= 0:
                    continue
                ratio = difflib.SequenceMatcher(None, r["text"].lower(), o["text"].lower()).ratio()
                if ratio >= threshold:
                    leak = True
                    break
            if leak:
                dropped += 1
                continue
        keep.append(r)
    return keep, dropped


def write_transcript(session: Path, rows: list[dict], meta: dict, cfg: dict) -> Path:
    def _lang_note(primary, dist: dict) -> str:
        if len(dist) > 1:
            parts = ", ".join(f"{k} {v} segment" for k, v in sorted(dist.items(), key=lambda x: -x[1]))
            return f"`{primary}` ağırlıklı, KARIŞIK ({parts})"
        return f"`{primary}`"

    md = [f"# Toplantı Transkripti — {meta['title']}", ""]
    md += [f"- Kayıt klasörü: `{session}`",
           f"- Süre: {fmt_ts(meta['duration'])}",
           f"- Mikrofon kanalı (sen): {meta['mic_segments']} segment, dil "
           + _lang_note(meta['mic_lang'], meta.get('mic_langs') or {}),
           f"- Sistem kanalı (diğerleri): {meta['sys_segments']} segment, dil "
           + _lang_note(meta['sys_lang'], meta.get('sys_langs') or {})]
    if meta.get("speakers"):
        md.append(f"- Tespit edilen konuşmacılar: {', '.join(meta['speakers'])} (+ {cfg['me']['label']})")
    if meta.get("diar_device"):
        md.append(f"- Diarization cihazı: `{meta['diar_device']}`"
                  + ("  ⚠ MPS başarısız olduğu için CPU kullanıldı" if meta.get("diar_fellback") else ""))
    if meta.get("bleed_dropped"):
        md.append(f"- Mikrofona sızan {meta['bleed_dropped']} tekrar segment ayıklandı")
    md += ["", "---", "", "## Transkript", ""]

    prev = None
    for r in rows:
        spk = r["speaker"]
        if spk != prev:
            md.append("")
            prev = spk
        md.append(f"**[{fmt_ts(r['start'])}] {spk}:** {r['text']}")

    path = output_path(session, "transcript.md")
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    output_path(session, "transcript.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    # Aynı klasörde eski adlı bir kopya kaldıysa temizle, yoksa iki transkript görünür.
    for kind in ("transcript.md", "transcript.json"):
        old = session / kind
        if old.exists() and old != output_path(session, kind):
            old.unlink()

    # Ölçülmüş ASR bozulmalarını onar (ölçülen hata oranları %34 ile %100 arasında).
    # Yalnızca glossary.md'deki AÇIK eşlemeler; benzerlik tabanlı düzeltme yok.
    # Ham hali .raw.md olarak saklanır, geri alınabilir.
    if cfg.get("transcribe", {}).get("fix_terms", True):
        try:
            import fixterms
            rules = fixterms.load_rules()
            n = fixterms.fix(session, rules) if rules else 0
            if n:
                print(f"  {n} terim düzeltildi (ham hali .raw.md olarak saklandı)")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ terim düzeltme atlandı: {e}")
    return path


def do_transcribe(session: Path, cfg: dict) -> Path:
    import transcribe as T
    from progress import Progress

    mic_wav, sys_wav = session / "mic.wav", session / "sys.wav"
    for p in (mic_wav, sys_wav):
        if not p.exists():
            raise SystemExit(f"HATA: {p} yok. Önce kayıt yapman gerekiyor.")

    prog = Progress(session)
    print("\n[1/3] Transkript")
    # ÖNCE MİKROFON. Sıra 2026-09-28'de değişti: mikrofon yakın çekim ve temiz, dil
    # algılaması orada güvenilir. Sistem kanalı sıkıştırılmış ve gürültülü; kısa
    # pencerelerde (0.7 sn medyan) whisper İngilizceye düşüp İngilizce doldurma
    # uyduruyor. Mikrofonun UZUN pencerelerinden çıkan baskın dil, sistem kanalının
    # kısa pencereleri için önsel olarak kullanılıyor.
    #
    # Dil KİLİTLENMİYOR: sistem kanalının uzun pencereleri kendi kararını verir, yoksa
    # karşı taraf gerçekten İngilizce konuşurken whisper çeviri yapmaya başlar.
    mic = T.transcribe(mic_wav, cfg, session / "mic.transcript.json", session=session,
                       prog=prog, prog_prefix="mic")
    prior = mic.get("long_window_prior")
    if prior:
        print(f"  [dil] toplantı önseli mikrofon kanalından: {prior}")
    sysr = T.transcribe(sys_wav, cfg, session / "sys.transcript.json", session=session,
                        prog=prog, prog_prefix="sys", lang_prior=prior)

    meta = {
        "title": session.name,
        "duration": max([s["end"] for s in mic["segments"] + sysr["segments"]] or [0]),
        "mic_segments": len(mic["segments"]), "sys_segments": len(sysr["segments"]),
        "mic_lang": mic.get("language"), "sys_lang": sysr.get("language"),
        "mic_langs": mic.get("languages_used", {}), "sys_langs": sysr.get("languages_used", {}),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    sys_segs = sysr["segments"]
    print("\n[2/3] Konuşmacı ayrımı")
    if cfg["diarization"]["enabled"] and sys_segs:
        import diarize as D
        prog.stage("diarize")
        dia = D.diarize(sys_wav, cfg, session / "sys.diarization.json", prog=prog)
        sys_segs = D.assign_speakers(sys_segs, dia["turns"])
        meta["speakers"] = dia["speakers"]
        meta["diar_device"] = dia["diagnostics"].get("device")
        meta["diar_fellback"] = bool(dia["diagnostics"].get("mps_error"))
    else:
        reason = "kapalı" if not cfg["diarization"]["enabled"] else "sistem kanalında konuşma yok"
        print(f"  atlandı ({reason})")
        sys_segs = [{**s, "speaker": "DIGER"} for s in sys_segs]

    print("\n[3/3] Birleştirme")
    prog.stage("merge")

    # ZAMAN EKSENİ DÜZELTMESİ — birleştirmeden hemen önce, diarization'dan sonra.
    # avfoundation bildirdiği hızda örnek üretmiyor: dosya, gerçek geçen süreden
    # %5-18 kısa çıkıyor ve iki kanal farklı oranlarda kayıyor (ölçüldü 2026-10-21,
    # 119 kayıt). Düzeltmezsek uzun toplantının sonunda senin cümlelerin karşı
    # tarafınkilerle dakikalarca yanlış sıraya giriyor.
    #
    # Ses HAM kalıyor: `aresample` telafisi dosyayı duvar saatine oturtuyor ama
    # konuşmanın ortasına 103 ms sessizlik enjekte edip transkripti bozuyor
    # (ölçüldü: 1536 → 1506 kelime, %96.1 benzerlik; aynı dosyada iki çalıştırma
    # ise %100 aynı, yani fark gürültü değil). Burada metni bozmadan yalnızca
    # zaman damgaları taşınıyor.
    import timeline as TL
    tl = TL.save(session, cfg)
    # TEK KANALI düzeltmek iki kanalı düzeltmemekten KÖTÜ: kanallar farklı
    # oranlarda kayıyor, birini taşıyıp ötekini bırakmak farkı büyütür.
    if tl and not {"mic", "sys"} <= set(tl["maps"]):
        print(f"  ⚠ yalnızca {sorted(tl['maps'])} için harita var — "
              f"zaman ekseni düzeltmesi UYGULANMADI")
        tl = None
    if tl:
        mic_segs = TL.apply(tl["maps"].get("mic", []), mic["segments"])
        sys_segs = TL.apply(tl["maps"].get("sys", []), sys_segs)
        meta["timeline_corrected"] = True
        meta["timeline_drift_s"] = tl.get("drift_s", {})
        d = tl.get("drift_s", {})
        print(f"  zaman ekseni düzeltildi — kayıt sonunda eklenen: "
              + " · ".join(f"{k} {v:+.0f} sn" for k, v in d.items()))
    else:
        mic_segs = mic["segments"]
        meta["timeline_corrected"] = False
        print("  ⚠ ffmpeg ilerleme logu okunamadı — zaman ekseni DÜZELTİLMEDİ "
              "(kanallar birbirinden kaymış olabilir)")

    rows = merge(mic_segs, sys_segs, cfg["me"]["label"])
    rows, dropped = drop_bleed(rows, cfg["me"]["label"])
    # Süre de düzeltilmiş eksende olmalı; yukarıda dosya zamanından hesaplanmıştı.
    meta["duration"] = max([r["end"] for r in rows] or [meta.get("duration", 0)])
    meta["bleed_dropped"] = dropped
    # Başlıkta yalnızca transkriptte GERÇEKTEN geçen konuşmacıları listele.
    # Diarization bazen hiçbir konuşma segmentiyle örtüşmeyen bir etiket üretiyor;
    # onu raporda göstermek "kim bu SPEAKER_01" sorusuna yol açıyor.
    me = cfg["me"]["label"]
    used = sorted({r["speaker"] for r in rows if r["speaker"] != me})
    if meta.get("speakers"):
        ghosts = [s for s in meta["speakers"] if s not in used]
        if ghosts:
            meta["speakers_without_speech"] = ghosts
    meta["speakers"] = used
    path = write_transcript(session, rows, meta, cfg)
    print(f"  {len(rows)} satır -> {path}")

    from notify import notify
    langs = set((meta.get("mic_langs") or {})) | set((meta.get("sys_langs") or {}))
    lang_note = "/".join(sorted(langs)) if langs else "?"
    people = len(meta.get("speakers") or []) + (1 if meta["mic_segments"] else 0)
    prog.finish()
    notify("Transkript hazır — sırada /meeting-actions",
           f"{len(rows)} satır · {people} konuşmacı · {lang_note}",
           subtitle="Action item için Cowork'te /meeting-actions yaz",
           level="info", open_path=path)
    return path


def do_actions(session: Path, cfg: dict) -> Path:
    import actions as A

    tpath = find_output(session, "transcript.md")
    if tpath is None:
        raise SystemExit(f"HATA: {session} içinde transkript yok. "
                         f"Önce: meet transcribe \"{session}\"")
    print("\n[action item]")
    out = output_path(session, "action-items.md")
    text = A.generate(tpath.read_text(encoding="utf-8"), cfg, out)
    header = f"# Action Item Raporu — {session.name}\n\n"
    out.write_text(header + text.lstrip(), encoding="utf-8")
    print(f"  -> {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Toplantı işleme pipeline'ı")
    ap.add_argument("session", nargs="?", help="toplantı klasörü (boşsa en son kayıt)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--transcribe", action="store_true")
    ap.add_argument("--actions", action="store_true")
    ap.add_argument("--profile", choices=["steel", "glass", "both", "core"],
                    help="jargon profili; config'teki transcribe.jargon_profile'ı geçersiz kılar")
    ap.add_argument("--language", help="dili sabitle (tr/en); varsayılan: otomatik algıla")
    args = ap.parse_args()

    cfg = load_config()
    load_env()
    if args.profile:
        cfg["transcribe"]["jargon_profile"] = args.profile
        cfg["transcribe"].pop("jargon", None)
    if args.language:
        cfg["transcribe"]["language"] = args.language
    session = Path(args.session).expanduser() if args.session else latest_session(cfg)
    if not session.is_dir():
        raise SystemExit(f"HATA: klasör yok: {session}")
    print(f"Toplantı: {session}")

    if args.transcribe or args.all:
        do_transcribe(session, cfg)
    if args.actions or args.all:
        if cfg["actions"].get("enabled", False):
            do_actions(session, cfg)
        else:
            # Sessizce hiçbir şey yapma — kullanıcı komutun çalıştığını sanıyor.
            print(f"""
  Action item raporu terminalden üretilmiyor.

  Bu adım Cowork'teki 'meeting-actions' skill'i üzerinden, senin Claude aboneliğinle
  çalışıyor — Anthropic API key gerektirmiyor, ek maliyeti yok.

  YAP:  Cowork'ü aç ve şunu yaz:   /meeting-actions

  İşlenmemiş toplantıları bulup her biri için action-items.md üretir.
  Belirli bir toplantı istersen klasör adını söylemen yeterli:
      {session.name}

  (API yolunu tercih edersen: config.yaml -> actions.enabled: true yap ve
   .env içine ANTHROPIC_API_KEY ekle. Bu ücretlidir.)
""")

    print(f"\nBitti. Klasör: {session}")


if __name__ == "__main__":
    sys.exit(main())
