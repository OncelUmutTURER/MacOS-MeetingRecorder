"""Kaydın kendiliğinden durması: kulaklık kopması ve süre sınırı.

Kullanıcının bildirdiği durum (2026-10-21): "toplantı sonrasında kulaklığı
hızlıca kutusuna koyuyorum, bu sürede recording devam ediyor oluyor."

Ölçüm doğruladı: 168 otomatik başlatmanın 28'i hiç durmamış. 2026-10-20-1619
kaydında kulaklık 18:13'te koptu, kayıt 19:17'ye kadar sürdü.

Burada sınananlar — ikinci grup birinciden KRİTİK:
  1. kopan cihaz grace süresi dolunca durdurma kararı veriyor mu
  2. durdurmaMAsı gereken durumlarda susuyor mu (cihaz hiç görülmedi, cihaz geri
     geldi, grace dolmadı) — yanlış pozitif canlı toplantıyı keser
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import record as R  # noqa: E402


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'✓' if ok else '✗'}  {label}" + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def main() -> int:
    ok = True
    G = 90.0

    print("mikrofon kopma kararı — DURDURMASI gerekenler:")
    # cihaz 60 sn önce kayboldu, grace 90 → henüz değil; 100 sn sonra → durdur
    seen, since, stop = R.mic_gone_decision(False, True, None, 1000.0, G)
    ok &= check("ilk kayboluş anı damgalanıyor", (seen, since, stop), (True, 1000.0, False))
    _, _, stop = R.mic_gone_decision(False, True, 1000.0, 1000.0 + 89, G)
    ok &= check("89 sn sonra henüz durdurmuyor", stop, False)
    _, _, stop = R.mic_gone_decision(False, True, 1000.0, 1000.0 + 90, G)
    ok &= check("90 sn dolunca durduruyor", stop, True)

    print("\nmikrofon kopma kararı — DOKUNMAMASI gerekenler:")
    # Cihazı kayıt boyunca hiç göremediysek "yok" sonucu ad uyuşmazlığından da
    # gelebilir. Bu koşul olmasa adı eşleşmeyen her kayıt 90 saniyede kesilirdi.
    seen, since, stop = R.mic_gone_decision(False, False, None, 1000.0, G)
    ok &= check("hiç görülmemiş cihaz için durdurmuyor", (seen, since, stop),
                (False, None, False))
    _, _, stop = R.mic_gone_decision(False, False, None, 9e9, G)
    ok &= check("hiç görülmemiş cihaz, çok sonra da durdurmuyor", stop, False)
    # Kulaklık geri takıldı: sayaç sıfırlanmalı, yoksa eski damga birikip
    # bir sonraki kısa kopmada kaydı anında keser.
    seen, since, stop = R.mic_gone_decision(True, True, 1000.0, 1050.0, G)
    ok &= check("cihaz geri gelince sayaç sıfırlanıyor", (since, stop), (None, False))
    seen, since, stop = R.mic_gone_decision(False, True, None, 1050.0, G)
    ok &= check("geri gelişten sonra sayaç yeniden başlıyor", since, 1050.0)

    print("\nsüre sınırı:")
    ok &= check("sınırsız kayıt dolmuyor", R.limit_expired({}, 9e9), False)
    ok &= check("sınır dolmadan durmuyor",
                R.limit_expired({"stop_at": 2000.0}, 1999.0), False)
    ok &= check("sınır dolunca duruyor",
                R.limit_expired({"stop_at": 2000.0}, 2000.0), True)

    print("\notomatik kaydı duraklatma (kullanıcının asıl isteği):")
    # "Bazı konuşmaları kaydetmek istemiyorum ama kaydı durdur demem yeterli
    # olmuyor, kayıt tekrar başlıyor. Toggle'ı kapatırsam tekrar açmayı unuturum
    # diye korkuyorum." → duraklatma SÜRELİ ve kendiliğinden geri açılır.
    old = R.SNOOZE
    with tempfile.TemporaryDirectory() as tmp:
        R.SNOOZE = Path(tmp) / ".autostart-snooze"
        ok &= check("dosya yokken duraklatma yok", R.snooze_info(), None)
        R.set_snooze(15 * 60, "kullanıcı")
        info = R.snooze_info()
        ok &= check("15 dk konunca kalan ~900 sn",
                    info is not None and 895 < info["left"] <= 900, True)
        ok &= check("sebep saklanıyor", info["reason"], "kullanıcı")
        ok &= check("SÜRE DOLUNCA KENDİLİĞİNDEN AÇILIR",
                    R.snooze_info(now=time.time() + 16 * 60), None)
        R.clear_snooze()
        ok &= check("elle kaldırılabiliyor", R.snooze_info(), None)
        R.set_snooze(300, "guard")
        ok &= check("guard durdurmasından sonra da konuyor",
                    R.snooze_info()["reason"], "guard")
        R.SNOOZE.write_text("bozuk içerik")
        ok &= check("bozuk dosya kaydı ENGELLEMEZ", R.snooze_info(), None)
    R.SNOOZE = old

    print("\nguard akışı — hangi cihaz izleniyor:")
    # KRİTİK: kulaklık kopunca `_recover_mic` akışı MacBook mikrofonuna taşıyor
    # ve durumdaki cihaz adı değişiyor. Durumdaki GÜNCEL adı izlersek kopan
    # cihazı hiç göremeyiz ve kayıt yine saatlerce sürer.
    cfg = {"guard": {"stop_on_mic_gone": True, "mic_gone_s": 10,
                     "stop_cooldown_s": 60},
           "audio": {"mic_priority": ["Acme Headset BT100", "MacBook Pro Microphone"]}}
    present: dict[str, bool] = {"Acme Headset BT100": True,
                                "MacBook Pro Microphone": True}
    stopped: list[str] = []
    real_exists, real_stop = R.audiodev.input_exists, R._guard_stop
    R.audiodev.input_exists = lambda n: present.get(n, False)
    R._guard_stop = lambda cfg, st, stamp, log, title, body, subtitle: (
        stopped.append(log) or True)
    try:
        R._MIC_GONE.clear()
        R._MIC_GONE.update(watch=None, seen=False, since=None)
        st = {"mic": {"device": "Acme Headset BT100", "rank": 0}}
        ok &= check("kulaklık bağlıyken durdurmuyor",
                    R._check_mic_gone(cfg, st, "t"), False)
        ok &= check("izlenen cihaz kulaklık", R._MIC_GONE["watch"], "Acme Headset BT100")

        # kulaklık kutuya girdi; guard akışı MacBook mikrofonuna taşıdı
        present["Acme Headset BT100"] = False
        st = {"mic": {"device": "MacBook Pro Microphone", "rank": 1}}
        ok &= check("kopuş anında hemen durdurmuyor",
                    R._check_mic_gone(cfg, st, "t"), False)
        ok &= check("cihaz MacBook'a geçse de kulaklık izleniyor",
                    R._MIC_GONE["watch"], "Acme Headset BT100")
        R._MIC_GONE["since"] -= 11          # grace dolmuş gibi yap
        ok &= check("grace dolunca durduruyor",
                    R._check_mic_gone(cfg, st, "t"), True)
        ok &= check("gerekçe loglandı", "koptu" in (stopped[0] if stopped else ""), True)

        # ayar kapalıysa hiç karışmamalı
        R._MIC_GONE.update(watch=None, seen=False, since=None)
        off = {"guard": {"stop_on_mic_gone": False}, "audio": cfg["audio"]}
        ok &= check("stop_on_mic_gone: false iken sessiz",
                    R._check_mic_gone(off, st, "t"), False)

        # MacBook mikrofonuyla başlayan kayıt: cihaz kaybolmaz, kural devre dışı kalır
        R._MIC_GONE.update(watch=None, seen=False, since=None)
        st2 = {"mic": {"device": "MacBook Pro Microphone", "rank": 1}}
        ok &= check("dahili mikrofonla kayıt etkilenmiyor",
                    R._check_mic_gone(cfg, st2, "t"), False)
    finally:
        R.audiodev.input_exists, R._guard_stop = real_exists, real_stop
        R._MIC_GONE.update(watch=None, seen=False, since=None)

    print("\n`meet start` argümanları (başlık ve --for karışmamalı):")
    seen_args = []
    real_start = R.cmd_start
    R.cmd_start = lambda title=None, limit=None: seen_args.append((title, limit))
    try:
        for argv, want in (
            (["start"], (None, None)),
            (["start", "müşteri görüşmesi"], ("müşteri görüşmesi", None)),
            (["start", "--for", "30"], (None, 30.0)),
            (["start", "geyik", "--for", "15"], ("geyik", 15.0)),
            (["start", "--for", "60", "demo"], ("demo", 60.0)),
        ):
            seen_args.clear()
            R._start_from_args(argv)
            ok &= check(" ".join(argv), seen_args[0], want)
    finally:
        R.cmd_start = real_start

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
