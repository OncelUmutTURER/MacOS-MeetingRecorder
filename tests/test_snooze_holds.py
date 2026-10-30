"""Duraklatma GERÇEKTEN tutuyor mu — üç açık, üçü de yaşandı.

Kullanıcı 2026-10-30'da bildirdi: *"Kayıt almayı 15dk duraklat dedim. Kayıt
durduruldu dedi, geri sayımı göstermeye başladı ama yine de tekrar kayıt
başladı."*

Kodda üç ayrı yol vardı ve her biri tek başına bunu üretebilir:

1. **Bayat sayaç.** İzleyici `hot_since`'i yalnızca kaydı KENDİ başlattığında
   sıfırlıyordu. Kullanıcı menüden başlatırsa sayaç kayıt boyunca duruyor ve
   kayıt biter bitmez "15 saniyedir aktif" koşulu ANINDA sağlanıyor.
2. **Yazma sırası.** `cmd_snooze` önce kaydı durduruyordu; `cmd_stop` ilk
   saniyede durum dosyasını siliyor ama parça birleştirme saniyeler sürüyor.
   O pencerede ne kayıt durumu ne duraklatma dosyası var.
3. **Elle başlatma duraklatmayı tanımıyordu.** `meet start` duraklatma açıkken
   de kaydı başlatıyordu; menü çubuğu hem "⏸" hem "● KAYITTA" gösteriyordu.
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audiowatch as A  # noqa: E402
import record as R  # noqa: E402

CFG = {
    "audio": {"mic_priority": ["TestMic"], "output_profiles": [
        {"match_input": None, "output": "TestOut"}]},
    "autostart": {"start_after_s": 15, "stop_after_s": 1, "poll_s": 0,
                  "min_recording_s": 60, "require_apps": [], "stop_device": "TestOut",
                  "flap_window_s": 180, "flap_max": 2, "max_recording_s": 14400},
}


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'✓' if ok else '✗'}  {label}" + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def drive(ticks: int, state: dict, events: list, *, stop_after: int | None = None,
          start_outside_at: int | None = None) -> None:
    """watch_loop'u N tur döndürür. Kayıt DIŞARIDAN başlatılabilir."""
    n = {"i": 0}

    def should_run() -> bool:
        n["i"] += 1
        i = n["i"]
        if start_outside_at is not None and i == start_outside_at:
            # kullanıcı menüden başlattı — izleyicinin haberi yok
            state["rec"] = {"session_dir": "/tmp/x", "started_at": "2026-10-30T16:30:00",
                            "mic": {"part": 1, "device": "TestMic"}}
            events.append("dışarıdan-başladı")
        if stop_after is not None and i == stop_after:
            state["rec"] = None
            events.append("dışarıdan-durdu")
        return i <= ticks

    A.watch_loop(CFG, Path("/tmp/meet"), should_run=should_run)


def main() -> int:
    ok = True
    A._find_device = lambda name, is_input: (1 if is_input else 2)
    A.is_running = lambda did: did == 1      # mikrofon hep aktif, çıkış boşta
    A.selftest = lambda: None
    import notify as N
    N.notify = lambda *a, **k: None
    sys.modules["notify"].notify = N.notify

    state = {"rec": None}
    events: list[str] = []

    def run_fake(cmd, **kw):
        action = cmd[1] if len(cmd) > 1 else ""
        if action not in ("start", "stop"):
            class R0:
                returncode = 1
                stdout = stderr = ""
            return R0()
        events.append(action)
        state["rec"] = ({"session_dir": "/tmp/x", "started_at": "2026-10-30T16:30:00",
                         "mic": {"part": 1, "device": "TestMic"}}
                        if action == "start" else None)

        class Rr:
            returncode = 0
            stdout = stderr = ""
        return Rr()

    A.subprocess.run = run_fake
    import common
    common.read_state = lambda: state["rec"]
    sys.modules["common"].read_state = common.read_state

    old_snooze = R.SNOOZE
    tmp = Path(tempfile.mkdtemp())
    R.SNOOZE = tmp / ".autostart-snooze"
    try:
        print("1) ELLE başlatılan kayıt bitince ANINDA yeniden başlamamalı:")
        # start_after_s 15 sn; sahte zaman yok, yani gerçek süre geçmiyor.
        # Dışarıdan başlatılan kayıt 3. turda başlıyor, 6. turda duruyor.
        events.clear(); state["rec"] = None
        drive(12, state, events, start_outside_at=3, stop_after=6)
        starts = events.count("start")
        ok &= check(f"izleyici yeni kayıt başlatmadı (start={starts})", starts, 0)

        print("\n2) duraklatma varken izleyici hiç başlatmaz:")
        events.clear(); state["rec"] = None
        R.set_snooze(15 * 60, "kullanıcı")
        drive(10, state, events)
        ok &= check("start çağrısı yok", events.count("start"), 0)
        R.clear_snooze()

        print("\n3) `meet snooze` ÖNCE duraklatmayı yazar, SONRA durdurur:")
        # DİKKAT: record.py `read_state`'i doğrudan import ediyor; `common` üzerinden
        # yamamak onu DEĞİŞTİRMEZ. İlk sürümde test bu yüzden "stop hiç çağrılmadı"
        # dedi ve yanlış yere baktırdı.
        R.read_state = lambda: state["rec"]
        order: list[str] = []
        real_stop, real_set = R.cmd_stop, R.set_snooze

        def fake_stop():
            order.append("stop")
            state["rec"] = None
        R.cmd_stop = fake_stop
        R.set_snooze = lambda s, r="kullanıcı": (order.append("snooze"),
                                                 real_set(s, r))[1]
        state["rec"] = {"session_dir": "/tmp/x"}
        try:
            R.cmd_snooze("15")
        finally:
            R.cmd_stop, R.set_snooze = real_stop, real_set
        ok &= check("sıra: önce snooze, sonra stop", order, ["snooze", "stop"])
        ok &= check("duraklatma gerçekten yazıldı", R.snooze_info() is not None, True)

        print("\n4) ELLE başlatma duraklatmayı iptal eder (çelişkili durum kalmaz):")
        # cmd_start'ın ağır kısımlarına girmeden yalnızca duraklatma mantığını sına:
        # config'i sahte ver, cihaz arama adımında SystemExit ile çıkacak.
        R.load_config = lambda: {"audio": {"mic_priority": ["YokBoyleCihaz"],
                                           "sys_device": "YokBoyleCihaz",
                                           "manage_output": False},
                                 "paths": {"meetings_dir": str(tmp / "m")},
                                 "guard": {}, "post_process": {}}
        R.set_snooze(15 * 60, "kullanıcı")
        state["rec"] = None
        cleared = {"v": False}
        real_clear = R.clear_snooze
        R.clear_snooze = lambda: (cleared.__setitem__("v", True), real_clear())[1]
        try:
            R.cmd_start("toplanti-adi")      # cihaz bulunamaz → SystemExit
        except (SystemExit, Exception):      # noqa: BLE001 — amaç yalnızca ilk adım
            pass
        finally:
            R.clear_snooze = real_clear
        ok &= check("elle başlatmada duraklatma kaldırıldı", cleared["v"], True)

        print("\n5) OTOMATİK başlatma duraklatma varken REDDEDİLİR:")
        R.set_snooze(15 * 60, "kullanıcı")
        # DİKKAT: `die()` mesajı stdout'a basıp `sys.exit(1)` yapıyor, yani
        # istisnanın metni "1". Sebebi görmek için çıktıyı yakalamak gerekiyor.
        import contextlib, io
        buf = io.StringIO()
        refused = False
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                R.cmd_start("auto")
            except SystemExit:
                refused = True
            except Exception:  # noqa: BLE001
                refused = False
        said_why = "duraklat" in buf.getvalue().lower()
        ok &= check("sebebi söylendi", said_why, True)
        ok &= check("otomatik başlatma reddedildi", refused, True)
        ok &= check("duraklatma DURUYOR (iptal edilmedi)",
                    R.snooze_info() is not None, True)
        R.clear_snooze()
    finally:
        R.SNOOZE = old_snooze
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n6) kim başlattı/durdurdu iz bırakıyor mu:")
    rec = (ROOT / "src" / "record.py").read_text(encoding="utf-8")
    for frag, label in (
        ("def _caller", "_caller() var"),
        ('"start.log"', "başlatan kaydediliyor"),
        ('çağıran: {_caller()}', "durduran kaydediliyor"),
    ):
        ok &= check(label, frag in rec, True)
    wat = (ROOT / "src" / "audiowatch.py").read_text(encoding="utf-8")
    ok &= check("izleyici kayıt görünce sayacı sıfırlıyor",
                "hot_since = None" in wat.split("if recording:")[1][:900], True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
