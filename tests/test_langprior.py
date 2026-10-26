"""Kısa pencere dil önselini sınar.

İki grup: önselin uygulanması gerekenler ve UYGULANMAMASI gerekenler. İkincisi
daha kritik — gerçek İngilizce konuşmayı Türkçeye çekmek, kısa bir "Mm-hmm"u
yanlış etiketlemekten çok daha kötü.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from transcribe import apply_prior, channel_prior  # noqa: E402

MIN_S = 1.5


def main() -> int:
    ok = True

    # (süre, algılanan dil, güven, beklenen sonuç, açıklama)
    cases = [
        (0.6, "en", 0.95, "tr", "0.6 sn 'Mm-hmm' → önsele çekilir (güven yüksek olsa da)"),
        (0.9, "en", 0.70, "tr", "0.9 sn 'Thank you' → önsele çekilir"),
        (1.4, "en", 0.80, "tr", "eşiğin hemen altı → çekilir"),
        (1.5, "en", 0.80, "en", "eşikte → DOKUNULMAZ"),
        (4.0, "en", 0.60, "en", "uzun gerçek İngilizce → DOKUNULMAZ (güven düşük olsa da)"),
        (12.0, "en", 0.99, "en", "uzun cümle → DOKUNULMAZ"),
        (0.5, "tr", 0.90, "tr", "zaten önselle aynı → değişmez"),
    ]
    chunks = [(0.0, d) for d, *_ in cases]
    raw = [(l, c) for _, l, c, *_ in cases]
    out, changed = apply_prior(raw, chunks, "tr", MIN_S)

    print("  önsel = 'tr' (Türkçe toplantı)\n")
    for (dur, det, conf, want, why), (got, _) in zip(cases, out):
        good = got == want
        ok &= good
        print(f"  {'✓' if good else '✗'}  {dur:5.1f} sn  {det} → {got:<3} "
              f"(beklenen {want})   {why}")
    print(f"\n  değiştirilen pencere: {changed} (beklenen 3)")
    ok &= changed == 3

    print("\n— önsel yoksa hiçbir şey değişmemeli —")
    out2, ch2 = apply_prior(raw, chunks, None, MIN_S)
    good = out2 == raw and ch2 == 0
    ok &= good
    print(f"  {'✓' if good else '✗'}  prior=None → {ch2} değişiklik")

    print("\n— önsel yalnızca UZUN pencerelerden hesaplanır —")
    # 5 kısa 'en' + 2 uzun 'tr' → önsel 'tr' olmalı (kısa olanlar oy kullanamaz)
    ch = [(0, 0.5)] * 5 + [(0, 6.0)] * 2
    rw = [("en", 0.9)] * 5 + [("tr", 0.9)] * 2
    p = channel_prior(rw, ch, MIN_S)
    good = p == "tr"
    ok &= good
    print(f"  {'✓' if good else '✗'}  5 kısa 'en' + 2 uzun 'tr' → önsel {p!r} (beklenen 'tr')")

    # hiç uzun pencere yoksa önsel üretilmemeli
    p2 = channel_prior([("en", 0.9)] * 4, [(0, 0.5)] * 4, MIN_S)
    good = p2 is None
    ok &= good
    print(f"  {'✓' if good else '✗'}  hiç uzun pencere yok → önsel {p2!r} (beklenen None)")

    print("\n— gerçekten iki dilli toplantıda önsel ÜRETİLMEMELİ —")
    # uzun pencereler yarı yarıya: tek bir toplantı dili yok
    ch4 = [(0, 5.0)] * 5 + [(0, 5.0)] * 5
    rw4 = [("tr", 0.9)] * 5 + [("en", 0.9)] * 5
    p4 = channel_prior(rw4, ch4, MIN_S, 0.7)
    good = p4 is None
    ok &= good
    print(f"  {'✓' if good else '✗'}  %50/%50 → önsel {p4!r} (beklenen None)")
    # %80 baskınlık yeterli
    ch5 = [(0, 5.0)] * 8 + [(0, 5.0)] * 2
    rw5 = [("tr", 0.9)] * 8 + [("en", 0.9)] * 2
    p5 = channel_prior(rw5, ch5, MIN_S, 0.7)
    good = p5 == "tr"
    ok &= good
    print(f"  {'✓' if good else '✗'}  %80 baskın → önsel {p5!r} (beklenen 'tr')")

    print("\n— İngilizce toplantı: önsel 'en', Türkçe kısa pencere çekilmeli —")
    ch3 = [(0, 0.6), (0, 8.0)]
    rw3 = [("tr", 0.9), ("en", 0.9)]
    out3, ch3n = apply_prior(rw3, ch3, "en", MIN_S)
    good = out3[0][0] == "en" and out3[1][0] == "en" and ch3n == 1
    ok &= good
    print(f"  {'✓' if good else '✗'}  simetrik çalışıyor ({ch3n} değişiklik)")

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
