"""Transkript katmanı — mlx-whisper (Apple Silicon Metal) + large-v3, VAD ön işlemeli.

Neden VAD: iki kanallı kayıtta her kanal zamanın yarısından fazlasında sessizdir
(sen konuşurken sys kanalı boş, karşı taraf konuşurken mic kanalı boş). Whisper uzun
dijital sessizlikte halüsinasyon döngüsüne girer ve zaman damgalarını kaçırır. Bu yüzden
önce silero-vad ile konuşma bölgeleri bulunur, sadece o bölgeler modele verilir ve
zaman damgaları orijinal zaman eksenine geri kaydırılır.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

SR = 16000


def select_jargon(cfg: dict) -> list[str]:
    """Aktif profile göre terim listesini kurar, sırasını koruyarak tekilleştirir ve kırpar.

    Kısa tutmak kritik: ölçümde 10 terim 9/10, 40 terim 6/10 doğruluk verdi ve uzun
    listelerde whisper prompt'taki terimleri konuşulmadığı halde metne enjekte etti.
    """
    t = cfg["transcribe"]
    if t.get("jargon"):  # eski düz liste biçimi hâlâ destekleniyor
        terms = list(t["jargon"])
    else:
        sets = t.get("jargon_sets", {})
        profile = (t.get("jargon_profile") or "steel").lower()
        names = ["core", "customers"]
        names += ["steel", "glass"] if profile == "both" else \
                 [profile] if profile in sets else []
        terms = [x for n in names for x in sets.get(n, [])]

    seen, out = set(), []
    for x in terms:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)

    cap = int(t.get("max_prompt_terms", 16))
    if len(out) > cap:
        print(f"  ⚠ jargon listesi {len(out)} terim, {cap}'e kırpıldı "
              f"(uzun prompt doğruluğu düşürüyor). Atılanlar: {', '.join(out[cap:])}")
        out = out[:cap]
    return out


def build_prompt(cfg: dict) -> str | None:
    jargon = select_jargon(cfg)
    if not jargon:
        return None
    return cfg["transcribe"].get("prompt_template", "{jargon}").format(jargon=", ".join(jargon))


# --------------------------------------------------------------------------
# VAD
# --------------------------------------------------------------------------

def _load_audio(path: Path, highpass_hz: float = 80.0) -> np.ndarray:
    """16 kHz mono float32 olarak yükler ve gümbürtüyü süzer.

    Masa darbeleri, klavye, fan ve gövde titreşimi 100 Hz altında yoğunlaşır; konuşma
    enerjisi 300-3000 Hz'dedir. Bu bandı kesmek hem VAD'ı hem whisper'ı belirgin
    rahatlatır. Arşivdeki WAV'a dokunulmaz, filtre sadece işleme sırasında uygulanır.
    """
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != SR:
        n = int(round(len(mono) * SR / sr))
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype(np.float32)
    if highpass_hz and highpass_hz > 0 and len(mono) > 64:
        from scipy.signal import butter, sosfiltfilt
        sos = butter(2, highpass_hz / (SR / 2), btype="highpass", output="sos")
        mono = sosfiltfilt(sos, mono).astype(np.float32)
    return mono


def _rms_dbfs(a: np.ndarray) -> float:
    return float(20 * np.log10(max(float(np.sqrt((a ** 2).mean())), 1e-9)))


def _speech_chunks(audio: np.ndarray, cfg: dict) -> list[tuple[float, float]]:
    """Konuşma bölgelerini bulur ve modele verilecek pencerelere gruplar."""
    from silero_vad import get_speech_timestamps, load_silero_vad
    import torch

    v = cfg["transcribe"]
    model = load_silero_vad()
    regions = get_speech_timestamps(
        torch.from_numpy(audio), model, sampling_rate=SR,
        min_speech_duration_ms=250,
        min_silence_duration_ms=int(v.get("vad_min_silence_ms", 500)),
        speech_pad_ms=int(v.get("vad_pad_ms", 300)),
        return_seconds=True,
    )
    if not regions:
        return []

    max_len = float(v.get("max_chunk_s", 30))
    join_gap = float(v.get("vad_join_gap_s", 1.0))
    chunks: list[list[float]] = []
    for r in regions:
        s, e = float(r["start"]), float(r["end"])
        if chunks and s - chunks[-1][1] <= join_gap and e - chunks[-1][0] <= max_len:
            chunks[-1][1] = e
        else:
            chunks.append([s, e])
    return [(s, e) for s, e in chunks]


# --------------------------------------------------------------------------
# Halüsinasyon temizliği
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Dil belirleme
# --------------------------------------------------------------------------

def _detect_window_languages(audio: np.ndarray, chunks: list[tuple[float, float]],
                             cfg: dict) -> list[tuple[str, float]]:
    """Her konuşma penceresi için dil ve güven değeri.

    Kod çözmeden önce sadece encoder + bir token çalıştırır, bu yüzden ucuz.
    Model, transcribe()'ın kullandığı ModelHolder önbelleğinden gelir — ikinci
    bir kopya belleğe yüklenmez.
    """
    import mlx.core as mx
    from mlx_whisper import audio as maudio, decoding as mdec
    from mlx_whisper.transcribe import ModelHolder

    t = cfg["transcribe"]
    cands = [c.lower() for c in (t.get("language_candidates") or [])]
    model = ModelHolder.get_model(t["model"], mx.float16)

    out: list[tuple[str, float]] = []
    for cs, ce in chunks:
        piece = audio[int(cs * SR):int(ce * SR)]
        mel = maudio.log_mel_spectrogram(piece, n_mels=model.dims.n_mels,
                                         padding=maudio.N_SAMPLES)
        seg = maudio.pad_or_trim(mel, maudio.N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = mdec.detect_language(model, seg)
        p = probs[0] if isinstance(probs, list) else probs
        if cands:  # kararı sadece olası diller arasında ver
            sub = {k: p.get(k, 0.0) for k in cands}
            tot = sum(sub.values()) or 1e-9
            lang = max(sub, key=sub.get)
            out.append((lang, sub[lang] / tot))
        else:
            lang = max(p, key=p.get)
            out.append((lang, float(p[lang])))
    return out


def channel_prior(raw: list[tuple[str, float]], chunks: list[tuple[float, float]],
                  min_s: float, min_share: float = 0.7) -> str | None:
    """Kanalın baskın dilini YALNIZCA uzun pencerelerden hesaplar.

    Kısa pencerelerin oyu sayılmaz çünkü güvenilmez (aşağıdaki ölçüme bak).

    `min_share`: baskın dil uzun pencerelerin bu kadarını tutmuyorsa önsel ÜRETİLMEZ.
    Gerçekten iki dilli toplantılarda (müşteri görüşmesinde İngilizce, ekiple Türkçe)
    tek bir "toplantı dili" yoktur; böyle bir durumda zorlamak kazanç değil kayıptır.
    """
    votes: dict[str, int] = {}
    for (lang, _), (s, e) in zip(raw, chunks):
        if e - s >= min_s:
            votes[lang] = votes.get(lang, 0) + 1
    if not votes:
        return None
    top = max(votes, key=votes.get)
    return top if votes[top] / sum(votes.values()) >= min_share else None


def apply_prior(raw: list[tuple[str, float]], chunks: list[tuple[float, float]],
                prior: str | None, min_s: float) -> tuple[list[tuple[str, float]], int]:
    """Kısa pencerelerin dil kararını toplantının baskın diliyle değiştirir.

    ÖLÇÜM (86 toplantı, Türkçe ağırlıklı olanların sys kanalı):

    |                  | 'en' etiketli | 'tr' etiketli |
    |------------------|---------------|---------------|
    | satır            |           741 |        10.813 |
    | süre medyanı     |    **0.7 sn** |        2.5 sn |
    | kelime medyanı   |         **2** |             7 |
    | ≤3 kelime        |       **%75** |           %23 |
    | "Mm-hmm" türü    |       **%39** |            %0 |

    Yani Türkçe bir toplantıda İngilizce sanılan şeylerin dörtte üçü üç kelimeden
    kısa, %39'u doğrudan doldurma sesi ("Mm-hmm", "Uh-huh", "Thank you"); bir örnek
    Almanca bile çıktı ("Das war Kyrgyzstan"). 0.7 saniyelik seste dil tahmini yazı
    turadır ve whisper İngilizceye düşüp İngilizce doldurma UYDURUR — etiket metinle
    tutarlı olduğu için sonradan yakalanamaz.

    Uzun pencerelerde sorun yok: 'en' satırlarının yalnızca %7'si 8+ kelime, onlar da
    gerçek İngilizce konuşma. Bu yüzden eşik SÜREYE bakıyor, güvene değil — güven
    değeri bu pencerelerde yüksek çıksa da yanlış oluyor.

    Dil KİLİTLENMİYOR: uzun pencereler kendi kararını veriyor, yoksa whisper yabancı
    dildeki konuşmayı transkript etmek yerine çevirir (ayrı ve daha kötü bir hata).
    """
    if not prior:
        return raw, 0
    out, changed = [], 0
    for (lang, conf), (s, e) in zip(raw, chunks):
        if e - s < min_s and lang != prior:
            out.append((prior, conf))
            changed += 1
        else:
            out.append((lang, conf))
    return out, changed


def _smooth_languages(langs: list[tuple[str, float]], min_prob: float) -> list[str]:
    """Tek pencerelik dil sıçramalarını komşularla düzeltir.

    Kısa pencerelerde ("tamam", "evet") dil algılaması güvenilmez; komşuları hemfikirse
    ve pencerenin kendi güveni düşükse komşuların dilini kabul et.
    """
    res = [l for l, _ in langs]
    for i in range(len(res)):
        prev = res[i - 1] if i > 0 else None
        nxt = langs[i + 1][0] if i + 1 < len(langs) else None
        if prev and nxt and prev == nxt and res[i] != prev and langs[i][1] < min_prob:
            res[i] = prev
        elif prev and not nxt and res[i] != prev and langs[i][1] < min_prob:
            res[i] = prev
    return res


def _marker_languages(session: Path | None, chunks: list[tuple[float, float]]) -> list[str | None]:
    """Elle konulmuş dil işaretlerini pencerelere uygular.

    `meet lang en` kayıt sırasında {t, lang} yazar; o andan itibaren gelen pencereler
    o dile sabitlenir. Otomatik algılamayı ezer.
    """
    out: list[str | None] = [None] * len(chunks)
    if session is None:
        return out
    f = session / "lang_markers.json"
    if not f.exists():
        return out
    try:
        marks = sorted(json.loads(f.read_text()), key=lambda m: m["t"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return out
    for i, (cs, _ce) in enumerate(chunks):
        active = [m for m in marks if m["t"] <= cs + 1.0]
        if active:
            out[i] = active[-1]["lang"]
    return out


_WS = re.compile(r"\s+")


def _is_prompt_echo(text: str, jargon: list[str]) -> bool:
    """initial_prompt'taki terim listesinin metne sızdığını yakalar.

    Whisper düşük sinyalli pencerelerde prompt'u konuşulmuş gibi yazabiliyor. Gerçek
    bir kayıtta üç kez oldu: "<ürün adı>, <ürün adı>, Copilot, LLM, ladle,
    refractory, tundish, slagline, hot spot, pulpit, laser scan." — kimsenin
    söylemediği bir cümle.

    Yöntem: metinden bilinen terimleri ve noktalama işaretlerini çıkar. Geriye
    kayda değer bir şey kalmıyorsa ve yeterince terim eşleştiyse, bu bir liste
    tekrarıdır, cümle değildir. Terimin bir cümle içinde doğal geçmesi bu testi
    tetiklemez, çünkü o zaman geriye cümlenin kalanı kalır.
    """
    if not jargon or len(text) < 25:
        return False
    residue = text.lower()
    hits = 0
    for term in sorted(jargon, key=len, reverse=True):
        t = term.lower()
        if t in residue:
            hits += 1
            residue = residue.replace(t, " ")
    if hits < 4:
        return False
    residue = re.sub(r"[\s,.;:!?()\-–—'\"]+", "", residue)
    return len(residue) <= max(6, int(len(text) * 0.15))


def _is_loop(text: str, max_repeat: int = 5) -> bool:
    """Aynı kelime öbeğinin arka arkaya tekrar ettiği halüsinasyon döngüsünü yakalar."""
    words = _WS.sub(" ", text.strip().lower()).split()
    if len(words) < max_repeat * 2:
        return False
    for size in (1, 2, 3, 4):
        if len(words) < size * max_repeat:
            continue
        run, unit = 1, tuple(words[:size])
        for i in range(size, len(words) - size + 1, size):
            cur = tuple(words[i:i + size])
            if cur == unit:
                run += 1
                if run >= max_repeat:
                    return True
            else:
                run, unit = 1, cur
    return False


# --------------------------------------------------------------------------

def transcribe(path: Path, cfg: dict, out_json: Path | None = None,
               language: str | None = None, session: Path | None = None,
               prog=None, prog_prefix: str = "",
               lang_prior: str | None = None) -> dict:
    """Bir kanalı transkript eder.

    language:  None ise config'teki transcribe.language kullanılır; o da null ise
               dil kanal başına BİR KEZ algılanıp tüm pencerelere kilitlenir.
               Çağıran taraf (pipeline) diğer kanalda algılanan dili buradan geçirebilir.
    """
    import mlx_whisper

    t = cfg["transcribe"]
    opts: dict = {
        "path_or_hf_repo": t["model"],
        "initial_prompt": build_prompt(cfg),
        "word_timestamps": False,
        "condition_on_previous_text": False,
        "verbose": None,
    }
    forced = language or t.get("language")

    t0 = time.time()
    audio = _load_audio(path, float(t.get("highpass_hz", 80)))
    total_s = len(audio) / SR
    segments: list[dict] = []
    detected: str | None = None
    dropped = 0
    print(f"  [whisper] {path.name} — model={t['model'].split('/')[-1]}, "
          f"dil={forced or 'algılanacak'}")

    if t.get("vad", True):
        if prog:
            prog.stage(f"vad-{prog_prefix}" if prog_prefix else "vad-sys")
        chunks = _speech_chunks(audio, cfg)
        speech_s = sum(e - s for s, e in chunks)
        print(f"  [vad] {len(chunks)} konuşma penceresi, {speech_s:.0f}/{total_s:.0f} sn konuşma "
              f"({100 * speech_s / max(total_s, 1e-9):.0f}%)")
        if not chunks:
            lvl = _rms_dbfs(audio)
            if lvl > -40:
                print(f"  ⚠ UYARI: kanalda {lvl:.0f} dBFS enerji var ama VAD hiç konuşma bulamadı.\n"
                      f"    Muhtemelen gürültü (masa darbesi, klavye, fan) kaydedilmiş ya da\n"
                      f"    mikrofon çok uzakta / seviyesi çok düşük. Kayıt yeniden gözden geçirilmeli.")
            else:
                print(f"  [whisper] kanal sessiz ({lvl:.0f} dBFS), atlandı")
    else:
        chunks = [(0.0, total_s)]

    # --- her pencere için dil kararı ---------------------------------------
    win_lang: list[str] = []
    if chunks:
        if forced:
            win_lang = [forced] * len(chunks)
            detected = forced
        else:
            mode = t.get("language_detect", "window")
            raw = _detect_window_languages(audio, chunks, cfg)

            # Kısa pencereler toplantının baskın diline çekilir. Önsel dışarıdan
            # gelmediyse (ilk işlenen kanal) kanalın kendi uzun pencerelerinden.
            trust_s = float(t.get("lang_trust_min_s", 1.5))
            share = float(t.get("lang_prior_min_share", 0.7))
            own_prior = channel_prior(raw, chunks, trust_s, share)
            use_prior = lang_prior or own_prior
            raw, changed = apply_prior(raw, chunks, use_prior, trust_s)
            if changed:
                src = "mikrofon kanalından" if lang_prior else "bu kanalın uzun pencerelerinden"
                print(f"  [dil] {changed} kısa pencere '{use_prior}' diline çekildi "
                      f"({src}, <{trust_s:g} sn güvenilmez)")

            if mode == "once":
                # en uzun pencerenin kararını tüm kanala uygula
                best = max(range(len(chunks)), key=lambda i: chunks[i][1] - chunks[i][0])
                win_lang = [raw[best][0]] * len(chunks)
            elif t.get("language_smooth", True):
                win_lang = _smooth_languages(raw, float(t.get("language_switch_min_prob", 0.6)))
            else:
                win_lang = [l for l, _ in raw]

            marks = _marker_languages(session, chunks)
            forced_by_mark = sum(1 for m in marks if m)
            win_lang = [m or l for m, l in zip(marks, win_lang)]
            if forced_by_mark:
                print(f"  [dil] {forced_by_mark} pencere elle konulan işaretle sabitlendi")

            counts: dict[str, int] = {}
            for l in win_lang:
                counts[l] = counts.get(l, 0) + 1
            detected = max(counts, key=counts.get)
            summary = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
            if len(counts) > 1:
                print(f"  [dil] KARIŞIK DİLLİ kanal — pencere dağılımı: {summary}")
            else:
                print(f"  [dil] {detected} ({len(chunks)} pencerenin tamamı)")

    # --- kod çözme ----------------------------------------------------------
    jargon_terms = select_jargon(cfg)
    echoed = 0
    langs_used: dict[str, int] = {}
    if prog:
        prog.stage(f"whisper-{prog_prefix}" if prog_prefix else "whisper-sys",
                   f"0/{len(chunks)} pencere")
    for i, (cs, ce) in enumerate(chunks, 1):
        opts["language"] = win_lang[i - 1]
        piece = audio[int(cs * SR):int(ce * SR)]
        r = mlx_whisper.transcribe(piece, **opts)
        for s in r.get("segments", []):
            text = s.get("text", "").strip()
            if not text:
                continue
            if _is_loop(text):
                dropped += 1
                continue
            if _is_prompt_echo(text, jargon_terms):
                echoed += 1
                continue
            segments.append({
                "start": round(cs + float(s["start"]), 2),
                "end": round(min(cs + float(s["end"]), ce), 2),
                "text": text,
                "lang": win_lang[i - 1],
            })
            langs_used[win_lang[i - 1]] = langs_used.get(win_lang[i - 1], 0) + 1
        print(f"  [whisper] pencere {i}/{len(chunks)} [{cs:.0f}-{ce:.0f} sn, "
              f"{win_lang[i-1]}] -> {len(segments)} segment", end="\r")
        if prog:
            prog.tick(i, len(chunks), f"{i}/{len(chunks)} pencere · {len(segments)} segment")

    segments.sort(key=lambda s: s["start"])
    dt = time.time() - t0
    print(" " * 70, end="\r")
    msg = f"  [whisper] {len(segments)} segment, {dt:.1f} sn, algılanan dil: {detected}"
    if dropped:
        msg += f", {dropped} halüsinasyon segmenti atıldı"
    if echoed:
        msg += f", {echoed} prompt sızıntısı atıldı"
    print(msg)

    out = {
        "source": str(path),
        "language": detected,
        # Uzun pencerelerden çıkan baskın dil. Diğer kanal bunu önsel olarak alır.
        "long_window_prior": locals().get("own_prior"),
        "lang_prior_applied": locals().get("use_prior") if locals().get("changed") else None,
        "languages_used": langs_used,
        "audio_duration_s": round(total_s, 2),
        "processing_s": round(dt, 1),
        "hallucination_segments_dropped": dropped,
        "prompt_echo_segments_dropped": echoed,
        "segments": segments,
    }
    if out_json:
        out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
