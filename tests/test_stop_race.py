"""İki durdurmanın aynı anda koşması — ve transkriptin hiç başlamaması.

Gerçek vaka, 2026-11-11-1448-auto (74 dakikalık toplantı). `stop.log`:

    === meet stop · 2026-11-11 16:03:08 ===     (iki kez, aynı saniye)
    ...
    33 mikrofon parçası birleştirildi            (iki kez)
    ⚠ parça temizliği yapılamadı: ... mic.part02.wav yok
    Bu toplantı için transkript zaten çalışıyor (pid 61592) ...
    Bu toplantı için transkript zaten çalışıyor (pid 61593) ...

Klasörde `process.log` yok, yani transkript hiç denenmedi. 61592/61593 ardışık
ve kısa ömürlü: onlar iki `meet stop`'un KENDİ `pgrep`'leriydi. `pgrep -f DESEN`
kendi sürecini eler ama aynı anda koşan başka bir pgrep'i elemez — desen onun da
komut satırında yazılıdır. Yani her durdurma ötekinin pgrep'ini "çalışan
transkript" sandı ve ikisi de vazgeçti.

İki savunma sınanıyor:
  1. `_real_pids` — pgrep/ps süreçleri elenir, GERÇEK süreçler elenmez.
  2. `_claim_stop` — oturum başına tek durdurma; ikincisi hiçbir şey yapmaz.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import record  # noqa: E402


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'✓' if ok else '✗'}  {label}"
          + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def test_filter() -> bool:
    """pgrep'in kendisi elenir, gerçek süreç elenmez."""
    ok = True
    fake = {
        11: "pgrep -f pipeline.py --transcribe /Users/x/Meetings/2026-11-11-1448",
        12: "/usr/bin/ps -o args= -p 11",
        13: "/opt/homebrew/bin/python3.12 /x/src/pipeline.py --transcribe /y",
        14: "",                     # süreç ölmüş, argv okunamadı
    }
    orig = record._argv
    record._argv = lambda pid: fake.get(pid, "")
    try:
        ok &= check("pgrep elendi", record._real_pids("11"), [])
        ok &= check("ps elendi", record._real_pids("12"), [])
        ok &= check("gerçek transkript süreci KALDI", record._real_pids("13"), [13])
        ok &= check("ölü süreç elenmedi (kararı pgrep'e bırak)",
                    record._real_pids("14"), [14])
        ok &= check("karışıkta yalnızca gerçeği döner",
                    record._real_pids("11 12 13"), [13])
    finally:
        record._argv = orig
    return ok


def test_filter_live() -> bool:
    """Asıl risk AŞIRI eleme: gerçek bir süreç yanlışlıkla elenmemeli.

    Komut satırında deseni taşıyan uzun ömürlü bir süreç kuruluyor; pgrep onu
    buluyor ve filtreden geçmesi gerekiyor.
    """
    pat = f"pipeline.py --transcribe /tmp/sahte-{os.getpid()}"
    # `sh -c "sleep 5"` tek komutta kendini exec ile DEĞİŞTİRİR ve desen komut
    # satırından kaybolur; ikinci bir komut bunu engelliyor.
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 5; :", pat],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.4)
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        found = record._real_pids(r.stdout)
        return check("gerçek süreç filtreden geçiyor", p.pid in found, True)
    finally:
        p.kill()
        p.wait()


def test_lock() -> bool:
    """Oturum başına tek durdurma; bayat kilit devralınır."""
    ok = True
    tmp = Path(tempfile.mkdtemp())
    try:
        ok &= check("ilk durdurma kilidi alır", record._claim_stop(tmp), None)
        ok &= check("kilit dosyası yazıldı", (tmp / ".stop.lock").exists(), True)

        # Başka bir YAŞAYAN sürecin kilidi: ikinci durdurma geri çekilmeli.
        other = subprocess.Popen(["sleep", "5"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            (tmp / ".stop.lock").write_text(str(other.pid))
            ok &= check("başkası tutuyorsa pid döner",
                        record._claim_stop(tmp), other.pid)
        finally:
            other.kill()
            other.wait()

        # ÖLÜ sahibin kilidi devralınmalı — yoksa bir çökme bütün sonraki
        # durdurmaları kilitler ve kayıt hiç kapanmaz.
        dead = subprocess.Popen(["true"])
        dead.wait()
        (tmp / ".stop.lock").write_text(str(dead.pid))
        ok &= check("bayat kilit devralınıyor", record._claim_stop(tmp), None)
        ok &= check("kilidi artık biz tutuyoruz",
                    (tmp / ".stop.lock").read_text().strip(), str(os.getpid()))

        (tmp / ".stop.lock").write_text("bozuk içerik")
        ok &= check("okunamayan kilit engel değil", record._claim_stop(tmp), None)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def test_wired() -> bool:
    """Düzeltmeler gerçekten durdurma yolunda mı."""
    ok = True
    src = (ROOT / "src" / "record.py").read_text(encoding="utf-8")
    for frag, label in (
        ("_claim_stop(session)", "cmd_stop kilidi alıyor"),
        ("_real_pids(r.stdout)", "pgrep çıktısı süzülüyor"),
    ):
        ok &= check(label, frag in src, True)
    ok &= check("ham pgrep çıktısı artık doğrudan kullanılmıyor",
                "pids = [int(p) for p in r.stdout.split()" in src, False)
    return ok


def main() -> int:
    print("pgrep kendi kendini görmesin:")
    ok = test_filter()
    ok &= test_filter_live()
    print("\neşzamanlı durdurma kilidi:")
    ok &= test_lock()
    print("\nbağlantı:")
    ok &= test_wired()
    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
