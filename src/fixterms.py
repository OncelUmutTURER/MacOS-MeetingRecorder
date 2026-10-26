"""Transkriptteki ölçülmüş ASR bozulmalarını onarır.

NEDEN AYRI BİR ADIM: whisper'ın `initial_prompt`'u 16 terimle sınırlı (ölçüldü: 40 terim
doğruluğu 9/10'dan 6/10'a düşürüyor ve terimleri metne enjekte ediyor). Yani sesi çözerken
elimizdeki kaldıraç küçük. Ama transkript YAZILDIKTAN sonra uzunluk maliyeti yok — orada
istediğimiz kadar terim düzeltebiliriz.

NEDEN BULANIK EŞLEME YOK: 82 transkriptte ölçüldü, benzerlik tabanlı düzeltme gerçek
konuşmayı bozuyor. `kafa` (47 kez, "Ali Bey kafa yoracak") Kafka değil · `face` (11,
"Hugging Face") FCE değil · `pilot` (38, "OVİ pilot istiyor") Copilot değil · `insight`
(80, "warning insight'ı ezer") bir ürün adı DEĞİL. Bu yüzden yalnızca `glossary.md` içindeki
**açık** "duyulan → doğrusu" tablosu uygulanır.

GÜVENLİK: ham transkript `<damga>-transcript.raw.md` olarak saklanır, her değişiklik
`<damga>-corrections.md` günlüğüne yazılır, `--undo` ile geri alınır.

Kullanım:
    fixterms.py <toplantı klasörü> [--dry-run] [--undo]
    fixterms.py --all [--dry-run]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (find_output, load_config, meetings_dir,  # noqa: E402
                    output_path, resolve_session, session_stamp)

GLOSSARY = Path(__file__).resolve().parent.parent / "glossary.md"

# Tablodaki sol sütun tırnak içinde virgülle ayrılmış varyantlar, sağ sütun **doğrusu**
ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*\*\*(.+?)\*\*\s*(?:\((.*?)\))?\s*\|\s*$")
# Sınırlayıcı olarak YALNIZCA çift tırnak. Kesme işareti Türkçe ekin parçası
# ("Helmut Beckert'ın", "DJX Spark'a") — sınırlayıcı sayılırsa ek kesilip atılıyor ve
# kural yanlış çalışıyor: `"Helmut Beckert'ın" -> Hewlett Packard'ın` kuralı
# `Helmut Beckert` diye okunup metne `Hewlett Packard'ın'in` yazıyordu.
QUOTED = re.compile(r"[\"“”]([^\"“”]+)[\"“”]")


def load_rules(path: Path | None = None) -> list[tuple[str, str]]:
    """glossary.md'deki 'Likely ASR mishearings' tablosunu okur.

    Yalnızca o başlıktan sonrası okunur; 'Do NOT auto-correct' tablosu ATLANIR.

    `path` varsayılan DEĞER olarak değil çağrı anında çözülüyor: varsayılan argüman
    import anında bağlanıyordu ve `fixterms.GLOSSARY`'yi testte değiştirmek hiçbir
    işe yaramıyordu (test sessizce yanlış dosyayı okuyordu).
    """
    path = path or GLOSSARY
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, l in enumerate(lines)
                     if l.startswith("## ") and "Likely ASR mishearings" in l)
    except StopIteration:
        return []
    rules: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        m = ROW.match(line)
        if not m:
            continue
        heard_cell, correct = m.group(1), m.group(2).strip()

        # BELİRSİZ KARŞILIKLAR ATLANIR. Sözlükte bazı satırların doğru sütunu seçenek
        # listesi ("pulpit ekibi / pulpit ekibiyle") ya da açıklama parantezi
        # ("refractory (ömrü)") içeriyor. Bunları olduğu gibi yazmak metne eğik çizgi
        # ve parantez sokar — okunabilir bir transkript yerine çöp üretir.
        # Bu satırlar rapor adımına bırakılır; orada bağlamla karar veriliyor.
        ambiguous = ("/" in correct) or ("(" in correct) or (")" in correct)

        for variant in QUOTED.findall(heard_cell):
            v = variant.strip()
            if not v or len(v) < 3 or v.lower() == correct.lower():
                continue
            # Koşullu notlar ("bağlam Node-RED ise") zaten tırnak dışında kalıyor.
            if ambiguous:
                skipped.append((v, correct))
                continue
            rules.append((v, correct))
    load_rules.skipped = skipped  # type: ignore[attr-defined]
    # uzun varyantı önce dene ki kısa olan içine girip bozmasın
    rules.sort(key=lambda r: -len(r[0]))
    return rules


def _pattern(variant: str) -> re.Pattern:
    """Kelime sınırlı, büyük/küçük harf duyarsız, Türkçe ekleri koruyan desen.

    'Blue Falcon'la' → 'BlueFalcon'la' olmalı; ek silinmemeli. Bu yüzden sınır olarak
    harf-olmayan ya da Türkçe ek başlangıcı (kesme işareti) kabul ediliyor.
    """
    parts = [re.escape(w) for w in variant.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<![0-9A-Za-zÇĞİÖŞÜçğıöşü]){body}(?![0-9A-Za-zÇĞİÖŞÜçğıöşü])",
                      re.IGNORECASE)


def apply_rules(text: str, rules: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str, int]]]:
    log: list[tuple[str, str, int]] = []
    for variant, correct in rules:
        pat = _pattern(variant)
        n = len(pat.findall(text))
        if n:
            text = pat.sub(correct, text)
            log.append((variant, correct, n))
    return text, log


def _paths(session: Path):
    stamp = session_stamp(session)
    md = find_output(session, "transcript.md")
    raw = session / f"{stamp}-transcript.raw.md"
    logf = session / f"{stamp}-corrections.md"
    return md, raw, logf


def undo(session: Path) -> bool:
    md, raw, logf = _paths(session)
    if not raw.exists():
        print(f"  {session.name}: ham yedek yok, geri alınacak bir şey yok")
        return False
    target = md or output_path(session, "transcript.md")
    target.write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
    raw.unlink()
    logf.unlink(missing_ok=True)
    print(f"  {session.name}: geri alındı")
    return True


def fix(session: Path, rules: list[tuple[str, str]], dry: bool = False) -> int:
    md, raw, logf = _paths(session)
    if md is None:
        return 0
    if raw.exists() and not dry:
        # zaten düzeltilmiş: ham hâlden yeniden uygula (kural listesi büyümüş olabilir)
        src = raw.read_text(encoding="utf-8")
    else:
        src = md.read_text(encoding="utf-8")

    fixed, log = apply_rules(src, rules)
    total = sum(n for _, _, n in log)
    if total == 0:
        return 0

    detail = " · ".join(f"{v}→{c} ({n})" for v, c, n in log[:6])
    print(f"  {session.name:<26} {total:>4} düzeltme   {detail}"
          + (" …" if len(log) > 6 else ""))
    if dry:
        return total

    if not raw.exists():
        raw.write_text(src, encoding="utf-8")
    md.write_text(fixed, encoding="utf-8")
    logf.write_text(
        f"# Terim düzeltmeleri — {session.name}\n\n"
        f"Ham hali: `{raw.name}`. Geri almak için: `meet fixterms \"{session}\" --undo`\n\n"
        "| Duyulan | Düzeltildi | Adet |\n|---|---|---|\n"
        + "\n".join(f"| {v} | {c} | {n} |" for v, c, n in log) + "\n",
        encoding="utf-8")
    return total


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    undo_mode = "--undo" in args
    do_all = "--all" in args
    targets = [a for a in args if not a.startswith("--")]

    rules = load_rules()
    if not rules and not undo_mode:
        raise SystemExit("HATA: glossary.md içinde 'Likely ASR mishearings' tablosu "
                         "bulunamadı — düzeltme kuralı yok.")
    skipped = getattr(load_rules, "skipped", [])
    print(f"{len(rules)} açık eşleme kuralı yüklendi"
          + ("  (KURU ÇALIŞTIRMA — dosya değişmeyecek)" if dry else ""))
    if skipped:
        print(f"{len(skipped)} kural ATLANDI (karşılığı belirsiz: '/' ya da parantez "
              f"içeriyor) — bunlar rapor adımında ele alınıyor")
    print()

    cfg = load_config()
    if do_all:
        sessions = sorted(d for d in meetings_dir(cfg).iterdir() if d.is_dir())
    elif targets:
        sessions = [resolve_session(t) for t in targets]
    else:
        # Klasör verilmediyse bulunduğun dizin
        sessions = [resolve_session(None)]

    if undo_mode:
        n = sum(1 for s in sessions if undo(s))
        print(f"\n{n} toplantı geri alındı.")
        return

    total = 0
    touched = 0
    for s in sessions:
        c = fix(s, rules, dry)
        total += c
        touched += 1 if c else 0
    print()
    print(f"{touched} toplantıda {total} düzeltme"
          + (" yapılacaktı (kuru çalıştırma)." if dry else " yapıldı."))
    if not dry and touched:
        print("Ham hâller *-transcript.raw.md olarak duruyor; geri alma: --undo")


if __name__ == "__main__":
    main()
