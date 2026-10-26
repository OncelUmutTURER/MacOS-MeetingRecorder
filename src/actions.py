"""Action item katmanı — birleşik transkripti Anthropic API'ye gönderir."""
from __future__ import annotations

import os
from pathlib import Path

# Rapor şablonu `docs/ACTION-ITEMS.md` ile AYNI olmalı: çoğu kullanıcı raporu bir
# sohbet asistanına yaptırıyor (API anahtarı gerekmiyor), bu dosya yalnızca API
# yolunu kullanmak isteyenler için. İkisi ayrışırsa raporlar birbirini tutmaz.
SYSTEM_TEMPLATE = """You are an experienced meeting secretary. You will be given a
meeting transcript with speaker labels and timestamps. The transcript may be in one
language or a mix of two, and it is automatic speech recognition output, so it
contains errors.

Rules:
- Write the entire report in {language}.
- Keep technical terms, product/company/site names and industry jargon EXACTLY as
  spoken; do not translate them.
- The label "{me}" is the user. Labels like SPEAKER_00, SPEAKER_01 are the other
  participants; if the transcript makes a real name recoverable (someone addresses
  them by name), add it in brackets: SPEAKER_01 (likely Ahmet).
- Invent nothing that is not in the transcript. Where unsure, write "unclear".
- If an action item has no owner or no date in the transcript, write "not stated".

Return exactly this markdown structure:

## Summary
(3-6 sentences)

## Decisions
- (decision) — evidence: [timestamp]
(if none: "No clear decision was taken in this meeting.")

## Action Items
| # | Task | Owner | Due | Evidence |
|---|---|---|---|---|
(if none, replace the rows with "No action items.")

## Open Questions
- (question left unanswered) — evidence: [timestamp]
"""


def generate(transcript_md: str, cfg: dict, out_path: Path | None = None) -> str:
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "HATA: ANTHROPIC_API_KEY yok.\n"
            "  ~/meeting-pipeline/.env dosyasına şu satırı ekle:  ANTHROPIC_API_KEY=sk-ant-..."
        )

    a = cfg["actions"]
    system = SYSTEM_TEMPLATE.format(
        language=a.get("report_language", "English"),
        me=(cfg.get("me") or {}).get("label", "Me"))
    client = anthropic.Anthropic(api_key=key)
    print(f"  [anthropic] {a['model']} çağrılıyor ({len(transcript_md)} karakter transkript)")
    try:
        resp = client.messages.create(
            model=a["model"],
            max_tokens=int(a.get("max_tokens", 8000)),
            system=system,
            messages=[{"role": "user", "content":
                       f"Aşağıdaki toplantı transkriptini işle.\n\n<transkript>\n{transcript_md}\n</transkript>"}],
        )
    except anthropic.NotFoundError as exc:
        raise SystemExit(
            f"HATA: '{a['model']}' modeli bulunamadı.\n"
            f"  config.yaml -> actions.model değerini geçerli bir model adıyla değiştir.\n"
            f"  Ayrıntı: {exc}"
        ) from exc
    except anthropic.AuthenticationError as exc:
        raise SystemExit(f"HATA: Anthropic API key geçersiz.\n  Ayrıntı: {exc}") from exc

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    print(f"  [anthropic] {resp.usage.input_tokens} girdi / {resp.usage.output_tokens} çıktı token")
    if out_path:
        out_path.write_text(text, encoding="utf-8")
    return text
