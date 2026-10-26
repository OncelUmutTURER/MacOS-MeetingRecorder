"""CoreAudio cihaz kullanım durumu izleyici.

Her ses cihazının `kAudioDevicePropertyDeviceIsRunningSomewhere` bayrağını okur —
"şu anda bir süreç bu cihazda aktif ses akışı açtı mı". Menü çubuğundaki turuncu
mikrofon noktasının altındaki mekanizma budur.

  meet watch --debug   canlı tablo; Teams'e girip çıkarken hangi bayrağın kalktığını gör
  meet watch           otomatik kayıt servisi (mikrofon aktifleşince başlat, bırakılınca durdur)

CoreAudio'ya ctypes ile doğrudan bağlanır; ek bağımlılık yok.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import sys
import time
from ctypes import byref, c_char_p, c_uint32, c_void_p, sizeof
from pathlib import Path

# --------------------------------------------------------------------------
# CoreAudio / CoreFoundation bağlayıcıları
# --------------------------------------------------------------------------

_ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

kAudioObjectSystemObject = 1


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", c_uint32), ("mScope", c_uint32), ("mElement", c_uint32)]


SCOPE_GLOBAL = _fourcc("glob")
SCOPE_INPUT = _fourcc("inpt")
SCOPE_OUTPUT = _fourcc("outp")
ELEM_MAIN = 0

PROP_DEVICES = _fourcc("dev#")
PROP_NAME = _fourcc("lnam")
# CoreAudio dört harfli kodları — yanlış yazarsan çağrı hata döner ve sessizce
# False elde edersin. Doğrusu:
#   kAudioDevicePropertyDeviceIsRunningSomewhere = 'gone'
#   kAudioDevicePropertyDeviceIsRunning          = 'goin'
PROP_RUNNING = _fourcc("gone")
PROP_STREAMS = _fourcc("stm#")

_cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, ctypes.c_long, c_uint32]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFRelease.argtypes = [c_void_p]
_ca.AudioObjectGetPropertyData.argtypes = [
    c_uint32, ctypes.POINTER(AudioObjectPropertyAddress), c_uint32, c_void_p,
    ctypes.POINTER(c_uint32), c_void_p]
_ca.AudioObjectGetPropertyDataSize.argtypes = [
    c_uint32, ctypes.POINTER(AudioObjectPropertyAddress), c_uint32, c_void_p,
    ctypes.POINTER(c_uint32)]


def _addr(sel: int, scope: int = SCOPE_GLOBAL) -> AudioObjectPropertyAddress:
    return AudioObjectPropertyAddress(sel, scope, ELEM_MAIN)


class CoreAudioError(RuntimeError):
    pass


def _get(obj: int, sel: int, ctype, scope: int = SCOPE_GLOBAL, strict: bool = False):
    a = _addr(sel, scope)
    val = ctype()
    size = c_uint32(sizeof(ctype))
    err = _ca.AudioObjectGetPropertyData(obj, byref(a), 0, None, byref(size), byref(val))
    if err != 0:
        if strict:
            # Sessizce False'a düşmek tehlikeli: yanlış bir selector kodu yüzünden
            # "hiçbir cihaz kullanımda değil" sanılıp saatlerce yanlış teşhis kondu.
            raise CoreAudioError(f"AudioObjectGetPropertyData hata {err} "
                                 f"(obj={obj}, sel={sel:#x})")
        return None
    return val


def device_ids() -> list[int]:
    a = _addr(PROP_DEVICES)
    size = c_uint32()
    if _ca.AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, byref(a), 0, None,
                                          byref(size)) != 0:
        return []
    n = size.value // sizeof(c_uint32)
    arr = (c_uint32 * n)()
    if _ca.AudioObjectGetPropertyData(kAudioObjectSystemObject, byref(a), 0, None,
                                      byref(size), arr) != 0:
        return []
    return list(arr)


def device_name(dev: int) -> str:
    ref = _get(dev, PROP_NAME, c_void_p)
    if not ref or not ref.value:
        return f"<{dev}>"
    buf = ctypes.create_string_buffer(512)
    ok = _cf.CFStringGetCString(ref.value, buf, 512, 0x08000100)  # kCFStringEncodingUTF8
    _cf.CFRelease(ref.value)
    return buf.value.decode("utf-8", "replace") if ok else f"<{dev}>"


def has_input(dev: int) -> bool:
    a = _addr(PROP_STREAMS, SCOPE_INPUT)
    size = c_uint32()
    if _ca.AudioObjectGetPropertyDataSize(dev, byref(a), 0, None, byref(size)) != 0:
        return False
    return size.value > 0


def is_running(dev: int) -> bool:
    v = _get(dev, PROP_RUNNING, c_uint32)
    return bool(v.value) if v is not None else False


def selftest() -> None:
    """Bayrak okumanın gerçekten çalıştığını doğrular.

    Bir kez yanlış dört harfli kod ('goon' yerine 'gone') yüzünden bütün cihazlar
    sürekli boşta göründü ve otomatik kayıt sessizce hiç tetiklenmedi. Bu kontrol
    o hatanın tekrar sessizce geçmesini engeller.
    """
    ids = device_ids()
    if not ids:
        raise CoreAudioError("hiç ses cihazı bulunamadı")
    bad = []
    for d in ids:
        try:
            _get(d, PROP_RUNNING, c_uint32, strict=True)
        except CoreAudioError as exc:
            bad.append(f"{device_name(d)}: {exc}")
    if bad:
        raise CoreAudioError("IsRunningSomewhere okunamıyor:\n  " + "\n  ".join(bad))


def snapshot() -> list[tuple[str, bool, bool]]:
    """(ad, giriş var mı, kullanımda mı)"""
    return [(device_name(d), has_input(d), is_running(d)) for d in device_ids()]


def app_running(names: list[str]) -> str | None:
    for n in names:
        if subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0:
            return n
        if subprocess.run(["pgrep", "-f", n], capture_output=True).returncode == 0:
            return n
    return None


# --------------------------------------------------------------------------
# Teşhis modu
# --------------------------------------------------------------------------

DEBUG_LOG = Path.home() / "Library" / "Logs" / "meeting-pipeline-watch.log"


def debug_loop(apps: list[str]) -> None:
    """Canlı tablo + geçişleri dosyaya yaz.

    Tablo ekranı temizleyerek yenilendiği için geçiş anı kaçabiliyor; asıl kayıt
    dosyaya düşen değişim satırlarıdır. Teams testinden sonra o dosyaya bakılır.
    """
    DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(DEBUG_LOG, "a", buffering=1)
    log.write(f"\n===== izleme başladı {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")

    print("CoreAudio cihaz kullanım izleyici — çıkmak için Ctrl+C\n")
    print("  Teams'te bir toplantıya katıl ve hangi satırların ● olduğunu izle.")
    print("  Aradığımız: sen toplantıya girince ● olan, çıkınca ○ olan cihaz.")
    print(f"\n  Tüm değişimler ayrıca şuraya yazılıyor:\n  {DEBUG_LOG}\n")
    time.sleep(2.5)

    prev: dict[str, bool] = {}
    prev_app: str | None = None
    recent: list[str] = []
    try:
        while True:
            rows = snapshot()
            app = app_running(apps)
            stamp = time.strftime("%H:%M:%S")

            if app != prev_app:
                line = f"[{stamp}] uygulama: {prev_app or 'yok'} -> {app or 'yok'}"
                log.write(line + "\n")
                recent.append(line)
                prev_app = app

            lines = []
            for name, inp, run in rows:
                mark = "●" if run else "○"
                kind = "IN " if inp else "OUT"
                was = prev.get(name)
                if was is not None and was != run:
                    line = (f"[{stamp}] {kind} {name}: "
                            f"{'BOŞTA -> KULLANIMDA ●' if run else 'KULLANIMDA -> BOŞTA ○'}")
                    log.write(line + "\n")
                    recent.append(line)
                prev[name] = run
                lines.append(f"  {mark} {kind} {name}")

            sys.stdout.write("\x1b[2J\x1b[H")
            print(f"CoreAudio cihazları — {stamp}   "
                  f"(izlenen uygulama: {app or 'çalışmıyor'})\n")
            print("\n".join(lines))
            print("\n  ● = bir süreç bu cihazda aktif ses akışı açmış")
            if recent:
                print("\n  --- son değişimler ---")
                for line in recent[-8:]:
                    print("  " + line)
            print(f"\n  log: {DEBUG_LOG}")
            time.sleep(1.5)
    except KeyboardInterrupt:
        log.write(f"===== izleme bitti {time.strftime('%H:%M:%S')} =====\n")
        log.close()
        print(f"\nbitti. Değişim kaydı: {DEBUG_LOG}")


# --------------------------------------------------------------------------
# Otomatik kayıt servisi
# --------------------------------------------------------------------------

FLAG = Path(__file__).resolve().parent.parent / ".autostart-enabled"


def autostart_on(cfg: dict) -> bool:
    """Otomatik kayıt açık mı.

    Menü çubuğundan açılıp kapanabilsin diye bir işaret dosyası kullanıyoruz.
    config.yaml yorumlarla dolu ve pyyaml ile yeniden yazınca hepsi kayboluyor.
    """
    if FLAG.exists():
        return True
    return bool((cfg.get("autostart") or {}).get("enabled", False))


def set_autostart(on: bool) -> None:
    if on:
        FLAG.touch()
    else:
        FLAG.unlink(missing_ok=True)


def _find_device(name: str, want_input: bool) -> int | None:
    """Adı eşleşen ve doğru YÖNE (giriş/çıkış) sahip cihazı bulur.

    Bluetooth kulaklıklar aynı adla iki cihaz olarak görünür (biri giriş, biri çıkış);
    yön filtresi olmadan yanlışını yakalarsın.
    """
    for d in device_ids():
        if name.lower() in device_name(d).lower() and has_input(d) == want_input:
            return d
    return None


def watch_loop(cfg: dict, meet: Path, should_run=None) -> None:
    """Toplantı başlayınca kaydı başlat, bitince durdur.

    ÖNEMLİ: başlatma ve durdurma AYRI cihazları izler.

      başlatma → MİKROFON girişi. Teams toplantıya girince mikrofonu açar.
                 Ölçüldü: çağrı başlarken tek seferde ● oluyor.
      durdurma → ÇIKIŞ (Multi-Output) cihazı. Teams çağrı boyunca çıkış akışını
                 kesintisiz açık tutuyor (ölçüldü: 42 sn hiç salınmadan).

    Neden aynı cihaz kullanılamaz: kayıt başladığı anda BİZİM ffmpeg'imiz mikrofonu
    ve BlackHole'u açıyor, o bayraklar bizim yüzümüzden sürekli ● kalıyor ve durdurma
    koşulu asla gerçekleşmiyor — kendi kendini besleyen bir döngü. Kaydedici hiçbir
    ÇIKIŞ cihazı açmadığı için çıkış bayrağı bu kirlenmeden muaf.
    """
    import audiodev
    import record
    from common import read_state
    from notify import notify

    selftest()   # bayrak okunamıyorsa sessizce hiç tetiklenmemektense hemen patla

    w = cfg.get("autostart", {})
    apps = w.get("require_apps") or []
    on_s = float(w.get("start_after_s", 15))
    off_s = float(w.get("stop_after_s", 25))
    poll = float(w.get("poll_s", 2))
    min_len = float(w.get("min_recording_s", 60))

    def start_name() -> str:
        if w.get("start_device"):
            return w["start_device"]
        prio = cfg["audio"].get("mic_priority") or [cfg["audio"].get("mic_device")]
        return next((p for p in prio if p and _find_device(p, True)), prio[0])

    def stop_name() -> str | None:
        if w.get("stop_device"):
            return w["stop_device"]
        target, _ = audiodev.pick_output_profile(cfg)
        return target

    print(f"Otomatik kayıt servisi başladı.\n"
          f"  başlatma sinyali : GİRİŞ  {start_name()}   ({on_s:.0f} sn aktif)\n"
          f"  durdurma sinyali : ÇIKIŞ  {stop_name()}   ({off_s:.0f} sn boşta)\n"
          f"  gereken uygulama : {', '.join(apps) if apps else '(yok)'}\n", flush=True)

    # --- çırpınma (flapping) koruması --------------------------------------
    # 2026-09-24: Google Meet toplantısında 67 klasör oluştu. Meet sesi
    # Multi-Output'a değil doğrudan kulaklığa gönderdiği için izlenen ÇIKIŞ cihazı
    # hep "boşta" göründü: 25 sn sonra durdur → mikrofon hâlâ aktif → 15 sn sonra
    # başlat → 45 saniyelik döngü, 73 dakika boyunca.
    #
    # Eşik tabanlı "son dakikada konuşma var mı" kontrolü denenmedi ÇÜNKÜ ölçüm
    # güvenilmez: gerçek bir toplantıda (2026-09-24-1317) mikrofonun 45 saniyelik
    # pencere RMS medyanı -64.9 dBFS, en düşüğü -98 dBFS — sessizlikle konuşmayı
    # seviyeye bakarak ayırmak yanlış karar verdirir.
    #
    # Bunun yerine EŞİKSİZ kural: durdurup hemen ardından yeniden başlıyorsak
    # durdurma sinyali yanlıştır. İki kez olduysa o sinyale bu oturum boyunca
    # güvenme, kaydı sürekli tut ve kullanıcıya söyle.
    flap_window = float(w.get("flap_window_s", 180))
    flap_max = int(w.get("flap_max", 2))
    max_len = float(w.get("max_recording_s", 4 * 3600))

    hot_since: float | None = None
    cold_since: float | None = None
    started_at: float | None = None
    last_auto_stop: float | None = None
    flaps = 0
    trust_stop = True
    snoozed = record.snooze_info() is not None

    while should_run is None or should_run():
        try:
            now = time.time()
            recording = read_state() is not None

            if not recording:
                # Otomatik kayıt duraklatılmış olabilir. İki sebep:
                #   kullanıcı — "bu sohbet kaydedilmesin" (menüden süreli duraklatma)
                #   guard     — kaydı kendi durdurdu; mikrofon hâlâ aktif olduğu için
                #               (Teams açıkken öyle oluyor) 15 sn sonra yeniden
                #               başlardı, iki çırpınmada durdurma sinyaline güven
                #               büsbütün kaybolurdu.
                info = record.snooze_info()
                if info:
                    snoozed = True
                    if int(info["left"]) % 60 < poll and info["left"] > poll:
                        print(f"  (otomatik kayıt duraklatıldı — "
                              f"{info['left'] / 60:.0f} dk kaldı, {info['reason']})",
                              flush=True)
                    time.sleep(poll)
                    continue
                if snoozed:
                    # Süre doldu. Bunu SÖYLEMEK şart: kullanıcı duraklatmayı
                    # unutacağından korktuğu için süreli yaptık, sessizce geri
                    # açılırsa o korku devam eder.
                    snoozed = False
                    record.clear_snooze()
                    print(f"[{time.strftime('%H:%M:%S')}] duraklatma bitti — "
                          f"otomatik kayıt yeniden aktif", flush=True)
                    notify("Otomatik kayıt yeniden aktif",
                           "Duraklatma süresi doldu, toplantılar yine kaydedilecek.",
                           level="info")
                dev = _find_device(start_name(), True)
                active = bool(dev is not None and is_running(dev))
                if apps and active:
                    active = app_running(apps) is not None
                if active:
                    hot_since = hot_since or now
                    if now - hot_since >= on_s:
                        # Az önce biz mi durdurduk? Öyleyse durdurma sinyali yanlış.
                        if last_auto_stop and now - last_auto_stop < flap_window:
                            flaps += 1
                            print(f"  ⚠ önceki kayıt {now - last_auto_stop:.0f} sn önce "
                                  f"durmuştu — çırpınma {flaps}/{flap_max}", flush=True)
                        else:
                            flaps = 0
                        print(f"[{time.strftime('%H:%M:%S')}] mikrofon {on_s:.0f} sn aktif "
                              f"— kayıt başlatılıyor", flush=True)
                        subprocess.run([str(meet), "start", "auto"], capture_output=True)
                        started_at, hot_since, cold_since = now, None, None
                        if trust_stop and flaps >= flap_max:
                            trust_stop = False
                            print("  ⚠ durdurma sinyaline artık güvenilmiyor — kayıt "
                                  "sürekli tutulacak", flush=True)
                            notify("⚠ Otomatik durdurma kapatıldı",
                                   "Toplantı uygulaması sesi Multi-Output'a göndermiyor "
                                   "olabilir; karşı taraf kaydedilmiyor olabilir.",
                                   subtitle="Kayıt sürüyor — bitince menüden durdur",
                                   level="error")
                else:
                    hot_since = None
            else:
                # Mutlak tavan HER ZAMAN geçerli. Eskiden yalnızca çırpınma yüzünden
                # durdurma sinyaline güvenilmediğinde bakılıyordu; oysa sinyale
                # güvenilen kayıtların da 28'i hiç durmadı (ölçüm: 2026-10-21).
                if started_at and now - started_at > max_len:
                    print(f"[{time.strftime('%H:%M:%S')}] kayıt {max_len/3600:.0f} "
                          f"saati aştı — güvenlik için durduruluyor", flush=True)
                    subprocess.run([str(meet), "stop"], capture_output=True, text=True)
                    notify("Kayıt otomatik durduruldu",
                           f"{max_len/3600:.0f} saatlik üst sınıra ulaşıldı.",
                           level="info")
                    started_at, cold_since, last_auto_stop = None, None, None
                    time.sleep(poll)
                    continue
                if not trust_stop:
                    time.sleep(poll)
                    continue

                sname = stop_name()
                dev = _find_device(sname, False) if sname else None
                # Çıkış cihazı bulunamıyorsa (kulaklık koptu vb.) kaydı KESME —
                # sessizce veri kaybetmektense fazladan kaydetmek yeğdir.
                active = True if dev is None else is_running(dev)
                if active:
                    cold_since = None
                else:
                    cold_since = cold_since or now
                    if now - cold_since >= off_s:
                        dur = now - (started_at or now)
                        # started_at yoksa bu kaydı biz başlatmadık: durum dosyası
                        # başka bir şey tarafından (guard rotasyonu gibi) yeniden
                        # yazılmış olabilir. Böyle bir "hayalet" durdurma her
                        # seferinde yeni bir transkript süreci doğuruyordu.
                        if started_at is None:
                            print(f"[{time.strftime('%H:%M:%S')}] ⚠ bizim başlatmadığımız "
                                  f"bir kayıt durumu görüldü — durdurma atlandı", flush=True)
                            cold_since = None
                            time.sleep(poll)
                            continue
                        print(f"[{time.strftime('%H:%M:%S')}] çıkış {off_s:.0f} sn boşta "
                              f"— kayıt durduruluyor ({dur:.0f} sn)", flush=True)
                        subprocess.run([str(meet), "stop"], capture_output=True, text=True)
                        if dur < min_len:
                            print(f"  ⚠ kayıt {dur:.0f} sn (< {min_len:.0f} sn) — "
                                  f"yanlış tetikleme olabilir", flush=True)
                        started_at, cold_since = None, None
                        last_auto_stop = now
        except Exception as exc:  # noqa: BLE001
            print(f"[uyarı] izleme döngüsünde hata: {exc}", flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_config

    cfg = load_config()
    apps = (cfg.get("autostart", {}) or {}).get("require_apps") or ["Microsoft Teams"]
    if "--debug" in sys.argv:
        debug_loop(apps)
    elif "--list" in sys.argv:
        for name, inp, run in snapshot():
            print(f"  {'●' if run else '○'} {'IN ' if inp else 'OUT'} {name}")
    else:
        watch_loop(cfg, Path(__file__).resolve().parent.parent / "bin" / "meet")
