"""Terim düzelticinin regresyon testi.

İki grup var ve **ikinci grup birincisinden daha önemli**: yanlış pozitif gerçek
konuşmayı bozar, kaçırılan bir düzeltme ise sadece eksik kalır.

Buradaki cümlelerin hepsi gerçek transkriptlerden alındı.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import fixterms  # noqa: E402
from fixterms import apply_rules, load_rules  # noqa: E402

# Testler kullanıcının özel `glossary.md`'sine BAĞLI OLMAMALI: temiz bir klonda
# o dosya yok (kuruma özel olduğu için .gitignore'da) ve test sessizce hiçbir şey
# sınamadan geçerdi. Kurallar bu fikstürden okunuyor.
FIXTURE = ROOT / "tests" / "fixtures" / "test-glossary.md"

# (girdi, içinde geçmesi gereken) — gerçek bozulmalar
MUST_FIX = [
    ("Bir kere Blue Falcon'la bu ana makina nasıl çalışacak", "BlueFalcon"),
    ("Bloo Falcon diye bir alet takıyoruz", "BlueFalcon"),
    ("Bluefalken'in ne yaptığını biliyor musun", "BlueFalcon"),
    ("Laptone yerine oradaki bütün termal görüntüler", "Lepton"),
    ("leptin'den olması şeyi bozuyor mu", "Lepton"),
    ("Termalleri laptondan alıyoruz", "Lepton'dan"),
    ("flörden veya başka bir sensörden", "FLIR'den"),
    ("Bu DJX Spark işte bir tane de gateway", "DGX Spark"),
    ("mini 640 modülünü taktık", "Mini640"),
    ("lazer scan sonuçları geldi", "laser scan"),
    ("argo cd üzerinden deploy ediyoruz", "ArgoCD"),
    ("North wind tarafında bir sorun var", "Northwind"),
    ("bütün ladlenin listesini çekiyor", "ladle'nin"),
    ("Telsic'e bir şirket mailine login olur musun", "Tailscale'e"),
    ("Haa, teliskel var mıymış?", "Tailscale"),
    ("Telscale ile devam edelim mi", "Tailscale"),
    ("Telescale peki bu durumda gateway'e kurulsa", "Tailscale"),
    ("Bunları elelemle anlamlandırıp ifadeler yazma", "LLM'le"),
    ("Musa bir elelem çalışması vardı", "LLM"),
    ("bu tür el elem kullanırken, lokal el elemler", "LLM'ler"),
    ("el elemle bunları generate etmeye çalışacaklar", "LLM'le"),
    ("Helmut Peckert'in Power Series bilgisayarları", "Hewlett Packard'ın"),
    ("bu Helmut Beckert'ın sistem gereksinimleri", "Hewlett Packard'ın"),
    ("Bu Halut Packard için konuşmak lazım", "Hewlett Packard"),
    ("Hal Packard'ın bilgisayarları 400 bin", "Hewlett Packard'ın"),
]

# (girdi) — DEĞİŞMEMESİ gereken gerçek cümleler
MUST_NOT_TOUCH = [
    "Ali Bey de kafa yoracak toplantıda",          # kafa != Kafka
    "Şeyden, Hugging Face'den indirdim",              # face != FCE
    "North Face'de gördüğüm en büyük indirim",        # face != FCE
    "Yani mesela ben şu an OVİ pilot istiyor",        # pilot != Copilot
    "warning, insight'ı ezer diye bir kuralımız",     # insight != Insite
    "bizim şu insight ile ilgili bir bug'ımız var",   # insight != Insite
    "Bu kampanya hâlâ devam ediyor aslında",          # kampanya: Türkçe, dokunma
    "NSC kampanyamız bu herhalde",                    # ek almış, dokunma
    "Mehmet sana şöyle söylesem",                      # gerçek isim, bozma
    "Bunun sebebini söyleyeyim Mehmet",                # gerçek isim, bozma
    "Bu tipe bir çözüm bulmamız lazım",               # type/tipe != TPE
    "master node üzerinde çalışıyor",                 # master != caster
    "bir cluster kurduk",                             # cluster != caster
    "Bu telkin edici bir şey değil",                  # telkin != Tailscale
    "Telsiz ile haberleşiyorlar",                     # telsiz != Tailscale
    "Telefonu açmadı",                                # telefon != Tailscale
    "telemetry verisi geliyor",                       # telemetry != Tailscale
    "Bu işi elle yapıyoruz",                          # elle != LLM
    "Eleme sınavı vardı",                             # eleme != LLM
    "Bir element eksik kalmış",                       # element != LLM
    "Yeni eleman aldık",                              # eleman != LLM
    "LMF'de sıcaklık düştü",                          # LMF != LLM (harf harf okunuşları benzer)
    "HP server'da 124 mi",                            # HP zaten doğru
    "Helmut Kohl Almanya başbakanıydı",               # gerçek isim != Hewlett Packard
    # Kesme işaretli kuralların ekleri KESMEMESİ gerekiyor. Parser bir ara kesmeyi
    # tırnak sınırlayıcısı sanıp eki yutuyordu ve metne 'Packard'ın'in' yazıyordu.
    "slagline'da sorun var",
    "ladle'nin listesi geldi",
    "DGX Spark'a gerek var mı",
]


def wiring_check() -> bool:
    """Otomatik yolun GERÇEKTEN düzeltme yaptığını uçtan uca doğrular.

    Toplantı bitince: record.cmd_stop → pipeline.py --transcribe → do_transcribe →
    write_transcript → fixterms. Buradaki test `write_transcript`'i doğrudan çağırır,
    yani ASR çıktısına bağlı kalmadan zincirin son halkasını sınar. Sahte bir toplantı
    klasöründe çalışır, sonra temizler.
    """
    import shutil
    import tempfile

    import pipeline
    # fixterms modül sabitini fikstüre çevir: zincirin son halkası
    # sınanıyor, kullanıcının sözlüğü değil.
    orig_glossary = fixterms.GLOSSARY
    fixterms.GLOSSARY = FIXTURE
    from common import find_output, load_config, session_stamp

    tmp = Path(tempfile.mkdtemp())
    session = tmp / "2026-01-02-0304-wiring-test"
    session.mkdir()
    try:
        cfg = load_config()
        rows = [
            {"start": 0.0, "speaker": "Me", "text": "Blue Falcon'la termalleri alıyoruz."},
            {"start": 5.0, "speaker": "SPEAKER_00", "text": "Laptone yerine DJX Spark koyalım."},
            {"start": 9.0, "speaker": "Me", "text": "Ali Bey de kafa yoracak."},
        ]
        meta = {"title": "wiring", "duration": 12.0, "mic_segments": 2, "sys_segments": 1,
                "mic_lang": "tr", "sys_lang": "tr", "mic_langs": {}, "sys_langs": {}}
        pipeline.write_transcript(session, rows, meta, cfg)

        md = find_output(session, "transcript.md")
        text = md.read_text(encoding="utf-8") if md else ""
        stamp = session_stamp(session)
        raw = session / f"{stamp}-transcript.raw.md"
        log = session / f"{stamp}-corrections.md"

        checks = [
            ("transkript yazıldı", bool(md)),
            ("BlueFalcon düzeltildi", "BlueFalcon" in text and "Blue Falcon" not in text),
            ("Lepton düzeltildi", "Lepton" in text and "Laptone" not in text),
            ("DGX Spark düzeltildi", "DGX Spark" in text and "DJX Spark" not in text),
            ("'kafa' korundu", "kafa yoracak" in text),
            ("ham yedek oluştu", raw.exists()),
            ("düzeltme günlüğü oluştu", log.exists()),
            ("ham yedekte orijinal duruyor",
             raw.exists() and "Blue Falcon" in raw.read_text(encoding="utf-8")),
        ]
        ok = True
        for label, good in checks:
            ok &= good
            print(f"  {'✓' if good else '✗'}  {label}")
        return ok
    finally:
        fixterms.GLOSSARY = orig_glossary
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    rules = load_rules(FIXTURE)
    skipped = getattr(load_rules, "skipped", [])
    print(f"{len(rules)} kural yüklendi · {len(skipped)} belirsiz kural atlandı\n")

    ok = True
    print("— düzeltilmesi gerekenler —")
    for text, expect in MUST_FIX:
        out, _ = apply_rules(text, rules)
        good = expect in out
        ok &= good
        print(f"  {'✓' if good else '✗'}  {out[:72]}")
        if not good:
            print(f"      beklenen: {expect!r}")

    print("\n— dokunulmaması gerekenler (daha kritik) —")
    for text in MUST_NOT_TOUCH:
        out, log = apply_rules(text, rules)
        good = out == text
        ok &= good
        print(f"  {'✓' if good else '✗'}  {text[:72]}")
        if not good:
            print(f"      BOZULDU: {out[:72]}")
            print(f"      kural  : {log}")

    print("\n— karşılığı belirsiz kurallar uygulanmamalı —")
    for bad in ("pulpit ekibi / pulpit ekibiyle", "refractory (ömrü)"):
        applied = any(c == bad for _, c in rules)
        ok &= not applied
        print(f"  {'✓' if not applied else '✗'}  {bad!r} uygulanmıyor")

    print("\n— otomatik yol: toplantı bitince düzeltme çalışıyor mu —")
    ok &= wiring_check()

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
