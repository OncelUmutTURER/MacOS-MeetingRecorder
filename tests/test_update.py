"""Yeni sürüm kontrolü — ağa çıkmadan.

Bu özellik kullanıcıya "yeni sürüm var" demekten ibaret; indirme ve kurulum
YAPMAZ. Dolayısıyla sınanması gereken üç şey var ve üçü de ağ gerektirmemeli:

  1. sürüm karşılaştırması doğru mu (ön sürüm etiketleri dahil)
  2. kapalıyken ya da aralık dolmadan ağa ÇIKMIYOR mu
  3. ağ hatası kaydı etkilemiyor mu — sessizce None dönmeli

Üçüncüsü en kritiği: güncelleme kontrolü yüzünden bir toplantının kaydı
etkilenirse özellik zararlıdır.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import update as U  # noqa: E402


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'✓' if ok else '✗'}  {label}" + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def main() -> int:
    ok = True

    print("sürüm karşılaştırma:")
    cases = [
        ("1.0.1", "1.0.0", True), ("1.1.0", "1.0.9", True), ("2.0.0", "1.9.9", True),
        ("1.0.0", "1.0.0", False), ("1.0.0", "1.0.1", False), ("0.9.9", "1.0.0", False),
        ("v1.2.0", "1.1.0", True),            # 'v' öneki
        ("1.2", "1.1.9", True),               # eksik yama hanesi
        ("1.10.0", "1.9.0", True),            # sayısal karşılaştırma, metin değil
    ]
    for remote, local, want in cases:
        ok &= check(f"{remote} > {local}", U.is_newer(remote, local), want)
    # Ön sürüm, aynı sayıların kararlı hâlinden ÖNCE gelir
    ok &= check("1.2.0-rc1 < 1.2.0", U.is_newer("1.2.0-rc1", "1.2.0"), False)
    ok &= check("1.2.0 > 1.2.0-rc1", U.is_newer("1.2.0", "1.2.0-rc1"), True)
    ok &= check("bozuk sürüm patlatmıyor", U.is_newer("", "1.0.0"), False)

    print("\nVERSION dosyası:")
    v = U.current_version()
    ok &= check(f"okunabiliyor ({v})", bool(v) and v[0].isdigit(), True)

    print("\nağa ÇIKMAMASI gereken durumlar:")
    calls = {"n": 0}
    real_fetch = U._fetch_latest
    U._fetch_latest = lambda repo: (calls.__setitem__("n", calls["n"] + 1),
                                    {"version": "9.9.9", "url": "u", "notes": ""})[1]
    real_cache = U.CACHE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            U.CACHE = Path(tmp) / ".update-check.json"

            calls["n"] = 0
            U.check({"updates": {"enabled": False, "repo": "a/b"}})
            ok &= check("kapalıyken hiç istek yok", calls["n"], 0)

            calls["n"] = 0
            U.check({"updates": {"enabled": True}})          # repo yok
            ok &= check("repo tanımsızken istek yok", calls["n"], 0)

            calls["n"] = 0
            cfg = {"updates": {"enabled": True, "repo": "a/b", "check_interval_h": 24}}
            res = U.check(cfg)
            ok &= check("ilk çağrı ağa çıkıyor", calls["n"], 1)
            ok &= check("yeni sürüm tespit edildi", bool(res and res["newer"]), True)

            res2 = U.check(cfg)
            ok &= check("aralık dolmadan TEKRAR çıkmıyor", calls["n"], 1)
            ok &= check("önbellekten aynı sonuç", res2["version"], "9.9.9")

            ok &= check("force ile çıkıyor", (U.check(cfg, force=True), calls["n"])[1], 2)

            # aralığı geçmişe çek: yeniden çıkmalı
            U.CACHE.write_text(json.dumps(
                {"checked_at": time.time() - 25 * 3600,
                 "latest": {"version": "9.9.9", "url": "u", "notes": ""}}),
                encoding="utf-8")
            U.check(cfg)
            ok &= check("aralık dolunca yeniden çıkıyor", calls["n"], 3)

            print("\nhata dayanıklılığı (kaydı ASLA etkilememeli):")
            U._fetch_latest = lambda repo: None               # ağ yok
            U.CACHE.unlink(missing_ok=True)
            ok &= check("ağ yokken None dönüyor", U.check(cfg), None)

            def boom(repo):
                raise OSError("ağ gitti")
            U._fetch_latest = boom
            U.CACHE.unlink(missing_ok=True)
            try:
                got = U.check(cfg)
                ok &= check("beklenmeyen hata yutuluyor, None dönüyor", got, None)
            except Exception as exc:  # noqa: BLE001
                ok &= check(f"beklenmeyen hata YUKARI SIZDI ({exc})", False, True)

            U.CACHE.write_text("{bozuk json", encoding="utf-8")
            ok &= check("bozuk önbellek dosyası patlatmıyor", U.read_cache(), {})
    finally:
        U._fetch_latest = real_fetch
        U.CACHE = real_cache

    print("\nkurulum komutu:")
    cmd = U.update_command()
    ok &= check("git pull içeriyor", "git pull" in cmd, True)
    ok &= check("kurulum yapmıyor, yalnızca komut veriyor",
                "install" not in cmd.replace("pip install -q -r requirements.txt", ""), True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
