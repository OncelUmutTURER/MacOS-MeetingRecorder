"""2026-09-24 Google Meet senaryosunu simüle eder ve çırpınma korumasını sınar.

Gerçekte olan: Meet sesi Multi-Output'a göndermediği için izlenen ÇIKIŞ cihazı hep
"boşta" göründü. Mikrofon ise toplantı boyunca aktifti. Sonuç 45 saniyelik
durdur/başlat döngüsü ve 73 dakikada 67 klasör.

Bu test aynı koşulları kurar: mikrofon HEP aktif, çıkış cihazı HİÇ aktif değil.
Beklenen: kayıt sayısı flap_max+1'de durur ve bir daha durdurma yapılmaz.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audiowatch as A  # noqa: E402

CFG = {
    "audio": {"mic_priority": ["TestMic"], "output_profiles": [
        {"match_input": None, "output": "TestOut"}]},
    # stop_device AÇIKÇA verilmeli: aksi halde pick_output_profile gerçek cihaz
    # listesine bakar, testte bulamaz ve durdurma zaten devre dışı kalır — test de
    # hiçbir şey sınamadan "geçer". (İlk sürümde tam olarak bu oldu.)
    "autostart": {"start_after_s": 1, "stop_after_s": 1, "poll_s": 0,
                  "min_recording_s": 60, "require_apps": [], "stop_device": "TestOut",
                  "flap_window_s": 180, "flap_max": 2, "max_recording_s": 14400},
}

state = {"rec": None}
events = []


def run_fake(cmd, **kw):
    # Yalnızca `meet start/stop` çağrılarını say. subprocess.run yaması paylaşılan
    # modülü değiştirdiği için buraya SwitchAudioSource gibi çağrılar da düşüyor.
    action = cmd[1] if len(cmd) > 1 else ""
    if action not in ("start", "stop"):
        class R0:
            returncode = 1
            stdout = stderr = ""
        return R0()
    events.append(action)
    if action == "start":
        state["rec"] = {"session_dir": "/tmp/x", "started_at": "2026-09-24T11:17:00",
                        "mic": {"part": 1, "device": "TestMic"}}
    elif action == "stop":
        state["rec"] = None

    class R:
        returncode = 0
        stdout = stderr = ""
    return R()


def main() -> int:
    A._find_device = lambda name, is_input: (1 if is_input else 2)
    # mikrofon (giriş, id 1) HEP aktif · çıkış (id 2) HİÇ aktif değil — Meet senaryosu
    A.is_running = lambda did: did == 1
    A.selftest = lambda: None
    A.subprocess.run = run_fake

    import common
    common.read_state = lambda: state["rec"]
    sys.modules["common"].read_state = common.read_state

    import notify as N
    N.notify = lambda *a, **k: None
    sys.modules["notify"].notify = N.notify

    ticks = {"n": 0}

    def should_run():
        ticks["n"] += 1
        return ticks["n"] < 4000

    t0 = time.time()
    A.time = _FakeTime(t0)
    A.watch_loop(CFG, Path("/bin/true"), should_run=should_run)

    starts = events.count("start")
    stops = events.count("stop")
    print(f"  başlatma: {starts} · durdurma: {stops}")
    print(f"  olay sırası: {' '.join(events[:12])}{' ...' if len(events) > 12 else ''}")

    ok = True
    if starts > CFG["autostart"]["flap_max"] + 1:
        print(f"  ✗ {starts} kayıt başladı — çırpınma durdurulamadı "
              f"(en fazla {CFG['autostart']['flap_max'] + 1} olmalı)")
        ok = False
    else:
        print(f"  ✓ kayıt sayısı {starts} ile sınırlandı (eski davranış: sınırsız)")
    if stops > CFG["autostart"]["flap_max"]:
        print(f"  ✗ {stops} kez durduruldu — durdurma sinyaline hâlâ güveniliyor")
        ok = False
    else:
        print(f"  ✓ durdurma {stops} kezden sonra kesildi")
    if state["rec"] is None:
        print("  ✗ kayıt açık kalmalıydı ama kapalı")
        ok = False
    else:
        print("  ✓ kayıt açık tutuluyor — toplantı kesilmiyor")
    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


class _FakeTime:
    """Gerçek zamanı beklemeden saati ilerletir; her yoklama 1 saniye sayılır."""

    def __init__(self, t0: float):
        self._t = t0

    def time(self) -> float:
        return self._t

    def sleep(self, _s: float) -> None:
        self._t += 1.0

    def strftime(self, fmt: str) -> str:
        return time.strftime(fmt)


if __name__ == "__main__":
    sys.exit(main())
