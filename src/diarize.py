"""Konuşmacı ayrımı — pyannote.audio.

MPS'te çalışmayı dener; başarısız olursa CPU'ya düşer ve bunu AÇIKÇA bildirir.
Sessizce kalite düşürmez: hangi cihazın kullanıldığı ve düşüş sebebi rapora yazılır.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DIAG: dict = {}


def _resolve_devices(pref: str) -> list[str]:
    import torch
    if pref == "cpu":
        return ["cpu"]
    if pref == "mps":
        return ["mps"]
    if torch.backends.mps.is_available():
        return ["mps", "cpu"]
    DIAG["mps_available"] = False
    return ["cpu"]


def diarize(path: Path, cfg: dict, out_json: Path | None = None, prog=None) -> dict:
    import torch
    from pyannote.audio import Pipeline

    d = cfg["diarization"]
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise SystemExit(
            "HATA: Hugging Face token yok.\n"
            "  ~/meeting-pipeline/.env dosyasına şu satırı ekle:  HF_TOKEN=hf_xxxxx\n"
            f"  Ayrıca {d['model']} model sayfasındaki kullanım koşullarını kabul etmiş olmalısın."
        )

    print(f"  [pyannote] model yükleniyor: {d['model']}")
    try:
        pipeline = Pipeline.from_pretrained(d["model"], token=token)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"HATA: pyannote modeli yüklenemedi.\n\n"
            f"  Neredeyse her zaman sebep: model sayfasındaki koşulları kabul etmemiş olman.\n"
            f"  Model CC-BY-4.0, ücretsiz ve lokal çalışıyor; kapı sadece bir iletişim formu.\n\n"
            f"  1. https://hf.co/{d['model']} aç\n"
            f"  2. 'Agree and access repository' de (şirket / kullanım amacı soruyor, anında onaylanır)\n"
            f"  3. meet process komutunu tekrar çalıştır\n\n"
            f"  Ayrıntı: {str(exc)[:400]}"
        ) from exc
    if pipeline is None:
        raise SystemExit(f"HATA: pyannote {d['model']} None döndü — token geçersiz olabilir.")

    kwargs = {}
    if d.get("min_speakers"):
        kwargs["min_speakers"] = int(d["min_speakers"])
    if d.get("max_speakers"):
        kwargs["max_speakers"] = int(d["max_speakers"])

    last_err = None
    for dev in _resolve_devices(d.get("device", "auto")):
        try:
            pipeline.to(torch.device(dev))
            print(f"  [pyannote] cihaz: {dev}")
            t0 = time.time()
            if prog is not None:
                # pyannote hook'u adım adım ilerleme veriyor; menü çubuğunda
                # "konuşmacılar ayrıştırılıyor · embeddings 40/128" gibi görünür.
                def _hook(step_name, step_artifact, file=None, total=None, completed=None):
                    if total:
                        prog.tick(completed or 0, total, f"{step_name} {completed or 0}/{total}")
                    else:
                        prog.tick(0, 1, str(step_name))
                kwargs["hook"] = _hook
            raw = pipeline(str(path), **kwargs)
            dt = time.time() - t0
            DIAG["device"] = dev
            DIAG["seconds"] = round(dt, 1)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if dev == "mps":
                DIAG["mps_error"] = str(exc)[:300]
                print(f"\n  ⚠ UYARI: pyannote MPS (Metal) üzerinde çalışmadı, CPU'ya düşülüyor.\n"
                      f"    Sebep: {str(exc)[:200]}\n"
                      f"    Sonuç kalitesi aynı, sadece daha yavaş olacak.\n")
                continue
            raise SystemExit(f"HATA: diarization başarısız ({dev}): {exc}") from exc
    else:
        raise SystemExit(f"HATA: diarization hiçbir cihazda çalışmadı: {last_err}")

    # community-1 bir DiarizeOutput döndürür: .speaker_diarization (örtüşmeli) ve
    # .exclusive_speaker_diarization (her an tek konuşmacı). İkincisi whisper
    # segmentleriyle eşleştirmek için tasarlandı, varsa onu tercih ediyoruz.
    annotation = None
    if d.get("use_exclusive", True):
        annotation = getattr(raw, "exclusive_speaker_diarization", None)
        if annotation is not None:
            DIAG["mode"] = "exclusive"
    if annotation is None:
        annotation = getattr(raw, "speaker_diarization", raw)
        DIAG.setdefault("mode", "overlapping")
    turns = [
        {"start": float(seg.start), "end": float(seg.end), "speaker": str(label)}
        for seg, _track, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda x: x["start"])
    turns, cleaned = _tidy_turns(
        turns,
        min_turn_s=float(d.get("min_turn_s", 0.4)),
        merge_gap_s=float(d.get("merge_gap_s", 0.6)),
    )
    if cleaned:
        DIAG["cleaned_turns"] = cleaned
        print(f"  [pyannote] {cleaned} mikro/parçalı sıra temizlendi")
    speakers = sorted({t["speaker"] for t in turns})
    print(f"  [pyannote] {len(turns)} konuşma sırası, {len(speakers)} konuşmacı: {', '.join(speakers)}"
          f"  ({DIAG['seconds']} sn, {DIAG['device']})")

    out = {"source": str(path), "diagnostics": DIAG, "speakers": speakers, "turns": turns}
    if out_json:
        out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _tidy_turns(turns: list[dict], min_turn_s: float, merge_gap_s: float) -> tuple[list[dict], int]:
    """Mikro sıraları ve gereksiz bölünmeleri temizler.

    Diarization, özellikle konuşma başlangıçlarında ve nefes/duraklamalarda saniyenin
    yüzde biri uzunluğunda sahte sıralar üretebiliyor ve tek konuşmacıyı ikiye
    bölebiliyor. Bunlar transkript segmentlerine atama yapılırken yanlış etiket
    doğuruyor. İki adım: (1) çok kısa sıraları at, (2) aynı konuşmacının küçük
    boşlukla ayrılmış ardışık sıralarını birleştir.
    """
    if not turns:
        return turns, 0
    kept = [t for t in turns if t["end"] - t["start"] >= min_turn_s]
    dropped = len(turns) - len(kept)
    if not kept:
        return turns, 0

    merged: list[dict] = [dict(kept[0])]
    for t in kept[1:]:
        last = merged[-1]
        if t["speaker"] == last["speaker"] and t["start"] - last["end"] <= merge_gap_s:
            last["end"] = max(last["end"], t["end"])
        else:
            merged.append(dict(t))
    return merged, dropped + (len(kept) - len(merged))


def assign_speakers(segments: list[dict], turns: list[dict], fallback: str = "SPEAKER_??") -> list[dict]:
    """Her whisper segmentine en çok zaman örtüşen diarization konuşmacısını atar."""
    out = []
    for seg in segments:
        best, best_ov = fallback, 0.0
        for t in turns:
            ov = min(seg["end"], t["end"]) - max(seg["start"], t["start"])
            if ov > best_ov:
                best, best_ov = t["speaker"], ov
        out.append({**seg, "speaker": best, "speaker_overlap_s": round(best_ov, 2)})
    return out
