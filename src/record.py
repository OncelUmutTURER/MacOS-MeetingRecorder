"""Kayıt katmanı.

Mikrofon ve sistem sesi (BlackHole loopback) AYRI ffmpeg süreçleriyle kaydedilir.
Ayrı süreç olmasının sebebi: toplantı ortasında kulaklık bağlanırsa mikrofon kanalını
yeni cihaza taşımak gerekiyor, bunu loopback kaydını kesmeden yapabilmeliyiz. Mikrofon
kanalı bu yüzden parçalar halinde yazılır (mic.part01.wav, part02...) ve durdururken
mutlak zaman damgalarına göre sessizlikle doldurularak tek mic.wav'a birleştirilir.

Hizalama: her iki süreç de -use_wallclock_as_timestamps kullanır ve ffmpeg her girdi
için mutlak epoch başlangıcını loguna yazar ("start: 1787233202.706233"). Parçaların
kaymasını tahmin etmek yerine bu değerden okuyoruz.

Kayıt boyunca bir "guard" süreci sistem çıkışını doğru Multi-Output cihazında tutar.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

import audiodev
from common import (FFMPEG, die, find_device, list_audio_devices, load_config,
                    meetings_dir, probe_channels, read_state, write_state)
from notify import notify

FFPROBE = "/opt/homebrew/bin/ffprobe"
ROOT = Path(__file__).resolve().parent.parent
ROOT_TONE = ROOT / "assets" / "loopback-test-tone.wav"
# Otomatik kaydın geçici olarak duraklatıldığı an. İki yerden konur:
#   · kullanıcı  — "bu sohbet kaydedilmesin" (menü: Otomatik Kaydı Duraklat)
#   · guard      — kaydı kendi durdurduktan sonra hemen yeniden başlamasın
# Süre dolunca KENDİLİĞİNDEN açılır. Kalıcı anahtarı (`.autostart-enabled`)
# kapatmanın sorunu tam olarak buydu: kullanıcı geri açmayı unutuyor.
SNOOZE = ROOT / ".autostart-snooze"


# ==========================================================================
# yardımcılar
# ==========================================================================

def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _stop_pid(pid: int | None, sig=signal.SIGINT, wait: float = 15.0) -> bool:
    """Süreci durdurur; ÖLDÜĞÜNÜ garanti eder. Öldüyse True.

    SIGKILL şart. Eskiden SIGINT→SIGTERM'den sonra pes ediliyordu; Bluetooth
    mikrofonundan okurken takılan ffmpeg ikisini de yutuyor ve süreç hayatta
    kalıyordu. Sonuç: 9 güne kadar yaşayan, mikrofonu açık tutan hayalet ffmpeg
    süreçleri (bir ölçümde aynı anda 10 tane). Hepsi aynı CoreAudio girişi için
    yarıştığı için yeni açılan akış sıfır örnek alıyor — guard'ın her toplantıda
    "dijital sessizlik" görüp rotasyon kotasını tüketmesinin gerçek sebebi buydu.
    """
    if not _alive(pid):
        return True
    os.kill(pid, sig)
    t0 = time.time()
    while _alive(pid) and time.time() - t0 < wait:
        time.sleep(0.2)
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return True
        t1 = time.time()
        while _alive(pid) and time.time() - t1 < 5.0:
            time.sleep(0.2)
    if _alive(pid):
        print(f"[stop] pid {pid} SIGINT/SIGTERM'e yanıt vermedi, SIGKILL", flush=True)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return True
        t2 = time.time()
        while _alive(pid) and time.time() - t2 < 3.0:
            time.sleep(0.2)
    if _alive(pid):
        print(f"[stop] ⚠ pid {pid} SIGKILL'den sonra da hayatta", flush=True)
        return False
    return True


def _reap_session_ffmpeg(session: Path, keep: set[int] | None = None) -> int:
    """Bu toplantıya yazan BÜTÜN ffmpeg süreçlerini toplar.

    Durum dosyası yalnızca güncel mic/sys pid'lerini bilir. Rotasyonda geride
    kalan ya da guard çökünce sahipsiz kalan ffmpeg'ler hiçbir yerde kayıtlı
    değil, dolayısıyla hiç öldürülmüyordu. Toplantı klasörünün yolunu komut
    satırında taşıyan her ffmpeg bu toplantıya aittir.
    """
    keep = keep or set()
    r = subprocess.run(["pgrep", "-f", f"ffmpeg.*{session}"],
                       capture_output=True, text=True)
    reaped = 0
    for line in r.stdout.split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid in keep or pid == os.getpid() or not _alive(pid):
            continue
        print(f"[stop] sahipsiz ffmpeg toplanıyor: pid {pid}", flush=True)
        if _stop_pid(pid, wait=3.0):
            reaped += 1
    return reaped


def orphan_ffmpeg(meetings_dir: Path, active: Path | None = None) -> list[dict]:
    """Aktif kayda ait olmayan, hâlâ yaşayan ffmpeg süreçlerini listeler.

    `meet check` ve `meet audit` bunu kullanır. Boş liste dönmeli.
    """
    r = subprocess.run(["ps", "-Ao", "pid=,etime=,args="], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or "ffmpeg" not in parts[2]:
            continue
        args = parts[2]
        if str(meetings_dir) not in args:
            continue
        if active is not None and str(active) in args:
            continue
        tgt = next((w for w in args.split() if str(meetings_dir) in w), "?")
        out.append({"pid": int(parts[0]), "etime": parts[1], "target": tgt})
    return out


def _slug(s: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_ " else "" for c in s)
    return "-".join(keep.split())[:60]


def _ffprobe(path: Path, entries: str) -> str:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", entries,
                        "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return r.stdout.strip()


def _dur(path: Path) -> float:
    try:
        return float(_ffprobe(path, "format=duration"))
    except ValueError:
        return 0.0


def _channels(path: Path) -> int:
    try:
        return int(_ffprobe(path, "stream=channels").splitlines()[0])
    except (ValueError, IndexError):
        return 0


def _mean_db(path: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else 0.0


_START_RE = re.compile(r"start:\s*(\d{9,}\.\d+)")


def _stream_epoch(log: Path) -> float | None:
    """ffmpeg logundan girdinin mutlak başlangıç epoch'unu okur."""
    if not log.exists():
        return None
    m = _START_RE.search(log.read_text(errors="replace")[:4000])
    return float(m.group(1)) if m else None


# ==========================================================================
# cihaz seçimi
# ==========================================================================

def _pick_mic(cfg: dict) -> tuple[str, int]:
    """Öncelik listesinden bağlı olan ilk mikrofonu seçer."""
    a = cfg["audio"]
    prio = a.get("mic_priority") or [a.get("mic_device")]
    names = {n.lower(): (i, n) for i, n in list_audio_devices()}
    for want in prio:
        if not want:
            continue
        for lname, (idx, real) in names.items():
            if want.lower() == lname or want.lower() in lname:
                return real, idx
    listing = "\n".join(f"  [{i}] {n}" for i, n in list_audio_devices())
    die(f"Öncelik listesindeki mikrofonların hiçbiri bağlı değil: {prio}\n"
        f"Mevcut giriş cihazları:\n{listing}")
    raise AssertionError


def _mic_rank(cfg: dict, name: str) -> int:
    prio = cfg["audio"].get("mic_priority") or [cfg["audio"].get("mic_device")]
    for i, p in enumerate(prio):
        if p and (p.lower() == name.lower() or p.lower() in name.lower()):
            return i
    return 99


# ==========================================================================
# ffmpeg başlatıcılar
# ==========================================================================

def _spawn(cmd: list[str], log_path: Path) -> subprocess.Popen:
    log = open(log_path, "wb")
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)


def _capture_cmd(cfg: dict, idx: int, out: Path) -> list[str]:
    """avfoundation yakalama komutu.

    `aresample=async=...` KULLANMA. Ölçüldü (2026-09-15): async telafisi kesintisiz
    bir test tonuna dakikada 10-76 kez, her biri ~102 ms SESSİZLİK enjekte ediyor.
    Sebep `min_hard_comp` varsayılanı: 0.1 saniye. `-use_wallclock_as_timestamps`
    her pakete ffmpeg'in onu ALDIĞI andaki duvar saatini yazıyor; avfoundation sesi
    öbekler halinde verdiği için "zaman damgası 100 ms ilerledi ama o kadar örnek
    gelmedi" durumu sürekli oluşuyor ve ffmpeg farkı sessizlikle kapatıyor.

    Gerçek toplantılarda aynı imza: sys.wav'da 621 adet 103±2.6 ms boşluk, konuşma
    ortasında, dakikada 6-13. mic.wav'da yalnızca 5 tane (mikrofon kanalı daha az
    öbekleniyor). Kullanıcı canlı dinlerken hiçbir kesinti duymadı — ses yolunda
    sorun yoktu, kayba giden tek yer bu filtreydi.

    Kontrollü test (kesintisiz 440 Hz ton, aynı cihaz, sırayla):
        wallclock + aresample=async=1000 : 38 kesilme / 30 sn
        aresample=async=1000 (wallclock yok): 31 kesilme
        wallclock + min_hard_comp=1.0    :  2 kesilme ama her biri 1024 ms
        wallclock, aresample YOK         :  0 kesilme   <-- seçilen
        ham                              :  0 kesilme

    `-use_wallclock_as_timestamps` KALIYOR: kanal hizalaması ffmpeg'in loguna
    yazdığı mutlak epoch'tan (`start: 1789478225.131475`) okunuyor, filtreden değil.
    """
    a = cfg["audio"]
    wsr = str(a["work_sample_rate"])
    cmd = [FFMPEG, "-hide_banner", "-y",
           "-use_wallclock_as_timestamps", "1", "-f", "avfoundation", "-i", f":{idx}"]
    if a.get("async_compensation", False):
        # Eski davranış. Sadece hizalama bozulursa ve sebebi kanıtlanırsa aç.
        cmd += ["-af", f"aresample=async=1000:first_pts=0:osr={wsr}"]
    else:
        cmd += ["-ar", wsr]
    return cmd + ["-ac", "1", "-c:a", "pcm_s16le", str(out)]


def _mic_cmd(cfg: dict, idx: int, out: Path) -> list[str]:
    return _capture_cmd(cfg, idx, out)


def _sys_cmd(cfg: dict, idx: int, out: Path) -> list[str]:
    return _capture_cmd(cfg, idx, out)


def _wait_for_data(proc: subprocess.Popen, target: Path, log: Path, label: str,
                   timeout: float = 45) -> None:
    warned = False
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            die(f"{label}: ffmpeg kapandı. Mikrofon izni reddedilmiş olabilir "
                f"(Sistem Ayarları > Gizlilik ve Güvenlik > Mikrofon) ya da cihaz meşgul.\n\n"
                + log.read_text(errors="replace")[-1200:])
        if target.exists() and target.stat().st_size > 16000:
            return
        if not warned and time.time() - t0 > 3:
            warned = True
            print(f"  ...{label} akışı bekleniyor. macOS mikrofon izni penceresi "
                  f"açıldıysa İZİN VER'e bas.")
        time.sleep(0.4)
    _stop_pid(proc.pid)
    die(f"{label}: {timeout:.0f} saniyedir hiç veri gelmedi, kayıt iptal edildi.")


# ==========================================================================
# start
# ==========================================================================

def cmd_start(title: str | None = None, limit_min: float | None = None) -> None:
    cfg = load_config()
    if (st := read_state()) is not None:
        if _alive(st.get("sys", {}).get("pid")) or _alive(st.get("mic", {}).get("pid")):
            die(f"Zaten kayıt sürüyor: {st['session_dir']}  (durdurmak için: meet stop)")
        write_state(None)

    a = cfg["audio"]
    mdir = meetings_dir(cfg)

    # Temiz mikrofonla başla. Geçmiş toplantılara yazan hayalet ffmpeg süreçleri
    # aynı CoreAudio girişini tuttuğu için yeni akış sıfır örnek alıyor.
    for o in orphan_ffmpeg(mdir):
        print(f"  önceki toplantıdan kalan ffmpeg temizleniyor: "
              f"pid {o['pid']} ({o['etime']}) → {Path(o['target']).parent.name}")
        _stop_pid(o["pid"], wait=3.0)

    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    session = mdir / (f"{stamp}-{_slug(title)}" if title else stamp)
    session.mkdir(parents=True, exist_ok=True)

    # --- çıkış cihazını doğru Multi-Output'a al -----------------------------
    prev_out = None
    if a.get("manage_output", True) and audiodev.available():
        prev_out = audiodev.current_output()
        # Kaydın öncesinde zaten Multi-Output'taysak bunu "önceki cihaz" diye saklama;
        # yoksa bitişte oraya geri döner ve cihaz sonsuza kadar orada kalır.
        if prev_out and prev_out.lower() in audiodev.managed_outputs(cfg):
            fixed, _ = audiodev.sane_idle_output(cfg, prev_out)
            print(f"  not: çıkış zaten '{prev_out}' (Multi-Output) — bitişte "
                  f"'{fixed}' cihazına dönülecek")
            prev_out = fixed
        target, why = audiodev.pick_output_profile(cfg)
        if target and target != prev_out:
            if audiodev.set_output(target):
                print(f"  çıkış: {prev_out} -> {target}  ({why})")
                time.sleep(1.5)
            else:
                print(f"  ⚠ çıkış '{target}' olarak ayarlanamadı — loopback boş çıkabilir")
        elif target:
            print(f"  çıkış zaten doğru: {target}")
        else:
            print(f"  ⚠ ÇIKIŞ AYARLANAMADI: {why}\n"
                  f"     Sistem çıkışı BlackHole içeren bir Multi-Output cihazında değilse\n"
                  f"     karşı tarafın sesi HİÇ kaydedilmez.")

    # --- cihazlar -----------------------------------------------------------
    mic_name, mic_idx = _pick_mic(cfg)
    sys_idx = find_device(a["sys_device"])
    rank = _mic_rank(cfg, mic_name)
    note = "" if rank == 0 else f"  (öncelikli cihaz bağlı değil, {rank+1}. tercih)"
    print(f"  mikrofon: [{mic_idx}] {mic_name}{note}")
    print(f"  sistem  : [{sys_idx}] {a['sys_device']}")

    # --- kayıt --------------------------------------------------------------
    sys_log = session / "sys.ffmpeg.log"
    p1_log = session / "mic.part01.log"
    sys_proc = _spawn(_sys_cmd(cfg, sys_idx, session / "sys.wav"), sys_log)
    mic_proc = _spawn(_mic_cmd(cfg, mic_idx, session / "mic.part01.wav"), p1_log)

    _wait_for_data(sys_proc, session / "sys.wav", sys_log, "sistem kanalı")
    _wait_for_data(mic_proc, session / "mic.part01.wav", p1_log, "mikrofon kanalı")

    state = {
        "session_dir": str(session),
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "prev_output": prev_out,
        "sys": {"pid": sys_proc.pid, "log": sys_log.name, "device": a["sys_device"]},
        "mic": {"pid": mic_proc.pid, "log": p1_log.name, "device": mic_name,
                "part": 1, "rank": rank},
        "parts": [{"n": 1, "file": "mic.part01.wav", "log": p1_log.name, "device": mic_name}],
    }
    if limit_min:
        state["limit_min"] = float(limit_min)
        state["stop_at"] = time.time() + float(limit_min) * 60
    write_state(state)
    print(f"\n● KAYIT BAŞLADI\n  {session}")
    if limit_min:
        print(f"  ⏱ süre sınırı: {limit_min:.0f} dakika "
              f"(değiştirmek için: meet limit <dk|off>)")
    # Arka arkaya toplantıda önceki kaydın transkripti hâlâ çalışıyor olabilir.
    # Menü çubuğu artık ikisini birden gösteriyor, ama kayda GİRERKEN de açıkça
    # söylemek gerekiyor: kullanıcı "kaydetmiyor" sanıp toplantıyı kaçırmasın.
    if _any_transcription_running():
        notify("● Kayıt başladı", "Önceki toplantının transkripti arka planda sürüyor.",
               subtitle="İkisi aynı anda çalışabilir — kayıt etkilenmez", level="info")
    _early_check(session)
    _loopback_selftest(cfg, session)

    # --- guard --------------------------------------------------------------
    if cfg.get("guard", {}).get("enabled", True):
        meet = Path(__file__).resolve().parent.parent / "bin" / "meet"
        glog = open(session / "guard.log", "wb")
        gproc = subprocess.Popen([str(meet), "guard"], stdin=subprocess.DEVNULL,
                                 stdout=glog, stderr=glog, start_new_session=True)
        # pid'i sakla ki stop guard'ı beklemek yerine doğrudan öldürebilsin
        state["guard_pid"] = gproc.pid
        write_state(state)
        print("  guard aktif: çıkış cihazı ve mikrofon değişimleri izleniyor")
    print("  Durdurmak için: meet stop")


def _early_check(session: Path) -> None:
    """İlk saniyelerde mikrofon dijital sessizlik mi veriyor.

    Bluetooth kulaklık mikrofonu HFP bağlantısı kötü kurulduğunda cihaz açılır, doğru
    örnekleme hızını bildirir, ama tam sıfır örnek gönderir. Bunu toplantı sonunda
    keşfetmek bir saatlik kaydı çöpe atmak demek.
    """
    time.sleep(3.0)
    mic = session / "mic.part01.wav"
    if not mic.exists() or mic.stat().st_size < 32000:
        return
    lvl = _mean_db(mic)
    if lvl < -80:
        print(f"\n  ⚠⚠ MİKROFON DİJİTAL SESSİZLİK VERİYOR ({lvl:.0f} dBFS).\n"
              "     Bluetooth kulaklıkta HFP bağlantısı kötü kurulmuş. YAP:\n"
              "       1. Kaydı durdur\n"
              "       2. Kulaklığı çıkar/tak, 20 saniye bekle\n"
              "       3. Mikrofon Kontrolü ile doğrula, sonra yeniden başlat\n")
        notify("⚠ Mikrofon sessiz", "Kulaklığı çıkarıp tak, 20 sn bekle, tekrar başlat.",
               subtitle="Bluetooth HFP bağlantısı kurulamamış", level="error")
    elif lvl < -60:
        print(f"  ⚠ mikrofon çok kısık ({lvl:.0f} dBFS) — mic.wav boş çıkabilir")
        notify("⚠ Mikrofon çok kısık", f"{lvl:.0f} dBFS — sesin kaydedilmeyebilir",
               level="warn")
    else:
        print(f"  ✓ mikrofon canlı ({lvl:.0f} dBFS)")


# ==========================================================================
# guard
# ==========================================================================

def cmd_guard() -> None:
    """Kayıt boyunca çıkış cihazını sabit tutar, daha iyi mikrofon bağlanırsa geçer."""
    cfg = load_config()
    g = cfg.get("guard", {})
    poll = float(g.get("poll_s", 4))
    rotate = bool(g.get("rotate_mic", True))
    print(f"[guard] başladı (poll {poll:.0f} sn, mikrofon rotasyonu {'açık' if rotate else 'kapalı'})",
          flush=True)

    while True:
        time.sleep(poll)
        st = read_state()
        if st is None:
            print("[guard] kayıt bitti, çıkılıyor", flush=True)
            return
        session = Path(st["session_dir"])
        stamp = time.strftime("%H:%M:%S")

        # 0) kaydın kendi kendine bitmesi gereken iki durum. İkisi de kaydı
        #    durdurup guard'ı sonlandırır, bu yüzden diğer kontrollerden önce.
        if _check_limit(cfg, st, stamp) or _check_mic_gone(cfg, st, stamp):
            return

        # 1) çıkış cihazı doğru Multi-Output'ta mı
        if cfg["audio"].get("manage_output", True) and audiodev.available():
            target, why = audiodev.pick_output_profile(cfg)
            cur = audiodev.current_output()
            if target and cur != target:
                ok = audiodev.set_output(target)
                print(f"[guard {stamp}] çıkış '{cur}' idi -> '{target}' "
                      f"({why}) {'✓' if ok else 'BAŞARISIZ'}", flush=True)
                if ok:
                    notify("Ses çıkışı düzeltildi", f"{cur} → {target}",
                           subtitle="Loopback kaydı korundu", level="quiet")
                else:
                    notify("⚠ Ses çıkışı düzeltilemedi",
                           f"'{target}' seçilemedi — karşı tarafın sesi kaydedilmiyor olabilir",
                           level="error")

        # 2) mikrofon dijital sessizliğe düştüyse akışı yeniden kur
        _recover_mic(cfg, st, session, stamp)

        # 3) loopback gerçekten ses alıyor mu
        _watch_silence(cfg, st, session, stamp)

        # 4) daha öncelikli bir mikrofon bağlandı mı
        if rotate:
            try:
                name, idx = _pick_mic(cfg)
            except SystemExit:
                continue
            if _mic_rank(cfg, name) < st["mic"].get("rank", 99):
                print(f"[guard {stamp}] daha öncelikli mikrofon bağlandı: {name} "
                      f"— mikrofon kanalı yeni parçaya taşınıyor", flush=True)
                _rotate_mic(cfg, st, session, name, idx)


# --------------------------------------------------------------------------
# guard'ın kaydı kendi kararıyla bitirdiği iki durum
# --------------------------------------------------------------------------
#
# NEDEN GEREKLİ (ölçüldü, 2026-10-21): otomatik izleyicinin durdurma sinyali
# ÇIKIŞ cihazının boşa düşmesi. 168 başlatmanın 140'ı böyle durmuş, ama 28'i hiç
# durmamış. Kanıt 2026-10-20-1619 kaydında:
#     17:46:31  mikrofon 2 dakikadır sessiz (HFP koptu)
#     18:13:26  guard: çıkış 'Meeting Output BT' -> 'Meeting Output SPK'
#               ^ bu satır tam olarak "kulaklık artık bağlı değil" demek
#     19:17:16  mic.wav'a son yazma
# Yani kulaklık 18:13'te kutuya girdi, kayıt 64 dakika daha sürdü. Sebep: kulaklık
# gidince izlenen cihaz Multi-Output SPK'ya kayıyor ve laptop hoparlöründen ses
# çıkan her şey (bildirim, tarayıcı, müzik) onu "kullanımda" tutuyor; 25 saniyelik
# boşluk hiç oluşmuyor. Kaydın son 5 dakikası dijital sessizlik DEĞİL (-25 dBFS),
# yani seviyeye bakarak anlamak da mümkün değildi.
#
# Eklenen sinyal eşiksiz ve ikili: kaydın mikrofonu olan CİHAZ ortadan kalktı mı.

_MIC_GONE: dict = {"watch": None, "seen": False, "since": None}


def mic_gone_decision(present: bool, seen: bool, since: float | None,
                      now: float, grace: float) -> tuple[bool, float | None, bool]:
    """(cihaz görüldü mü, ne zamandır yok, şimdi durdurulsun mu).

    `seen` koşulu kasıtlı: cihaz adını hiç eşleştiremediysek "yok" sonucu ad
    uyuşmazlığından da gelebilir ve kaydı sebepsiz keseriz. Kayıt sırasında cihazı
    bir kez görmeden asla durdurmayız.
    """
    if present:
        return True, None, False
    if not seen:
        return seen, None, False
    since = now if since is None else since
    return seen, since, (now - since) >= grace


def _check_mic_gone(cfg: dict, st: dict, stamp: str) -> bool:
    g = cfg.get("guard", {})
    if not g.get("stop_on_mic_gone", True):
        return False
    cur = (st.get("mic") or {}).get("device")
    if not cur:
        return False
    # Hangi cihazı izliyoruz: kaydın kullandığı EN ÖNCELİKLİ cihaz. Kulaklık
    # koptuktan sonra `_recover_mic` akışı MacBook mikrofonuna taşıyor; durumdaki
    # güncel adı izlersek kopan cihazı gözden kaçırırız.
    watch = _MIC_GONE["watch"]
    if watch is None or _mic_rank(cfg, cur) < _mic_rank(cfg, watch):
        _MIC_GONE.update(watch=cur, seen=False, since=None)
        watch = cur
    try:
        present = audiodev.input_exists(watch)
    except Exception:  # noqa: BLE001  — cihaz listelenemiyorsa kaydı KESME
        return False
    seen, since, stop = mic_gone_decision(
        present, _MIC_GONE["seen"], _MIC_GONE["since"], time.time(),
        float(g.get("mic_gone_s", 90)))
    _MIC_GONE.update(seen=seen, since=since)
    if not stop:
        if since and _MIC_GONE.get("logged") != since:
            _MIC_GONE["logged"] = since
            print(f"[guard {stamp}] '{watch}' bağlantısı koptu — "
                  f"{float(g.get('mic_gone_s', 90)):.0f} sn içinde geri gelmezse "
                  f"kayıt durdurulacak", flush=True)
        return False
    return _guard_stop(cfg, st, stamp,
                       log=f"'{watch}' {float(g.get('mic_gone_s', 90)):.0f} sn önce "
                           f"koptu — toplantı bitti sayılıyor",
                       title="Kayıt durduruldu",
                       body=f"{watch} bağlantısı kesildi, toplantı bitti sayıldı.",
                       subtitle="Transkript arka planda çıkarılıyor")


def limit_expired(st: dict, now: float) -> bool:
    at = st.get("stop_at")
    return bool(at) and now >= float(at)


def _check_limit(cfg: dict, st: dict, stamp: str) -> bool:
    if not limit_expired(st, time.time()):
        return False
    mins = float(st.get("limit_min") or 0)
    return _guard_stop(cfg, st, stamp,
                       log=f"süre sınırı doldu ({mins:.0f} dk)",
                       title="Kayıt süresi doldu",
                       body=f"{mins:.0f} dakikalık sınıra ulaşıldı, kayıt durduruldu.",
                       subtitle="Transkript arka planda çıkarılıyor")


def _guard_stop(cfg: dict, st: dict, stamp: str, log: str, title: str,
                body: str, subtitle: str) -> bool:
    """Kaydı guard içinden durdurur.

    `meet stop` guard'ın kendisini öldürüyor; bu yüzden ayrı oturumda başlatıp
    hemen dönüyoruz (çağıran `return` ediyor). Durumu BİZ yazmıyoruz — guard'ın
    write_state() ile durdurulmuş kaydı diriltmesi bu projede bir kez yaşandı.
    """
    if read_state() is None:          # bu arada elle durdurulmuş olabilir
        return True
    print(f"[guard {stamp}] {log} — kayıt durduruluyor", flush=True)
    set_snooze(float(cfg.get("guard", {}).get("stop_cooldown_s", 300)), "guard")
    meet = ROOT / "bin" / "meet"
    subprocess.Popen([str(meet), "stop"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    notify(title, body, subtitle=subtitle, level="info")
    return True


# --------------------------------------------------------------------------
# otomatik kaydı geçici duraklatma (snooze)
# --------------------------------------------------------------------------
#
# NEDEN BÖYLE: kullanıcı bazı konuşmaların kaydedilmesini istemiyor. "Kaydı
# durdur" yetmiyor çünkü mikrofon hâlâ aktifken izleyici 15 saniye sonra yeniden
# başlatıyor. Kalıcı anahtarı kapatmak da çözüm değil: "tekrar açmayı unuturum
# diye korkuyorum". Bu yüzden duraklatma SÜRELİ ve kendiliğinden geri açılıyor.

def set_snooze(seconds: float, reason: str = "kullanıcı") -> None:
    SNOOZE.write_text(json.dumps({"until": time.time() + float(seconds),
                                  "reason": reason}))


def snooze_info(now: float | None = None) -> dict | None:
    """Duraklatma sürüyorsa {'left': sn, 'reason': ...}; değilse None."""
    now = time.time() if now is None else now
    try:
        d = json.loads(SNOOZE.read_text())
        left = float(d["until"]) - now
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if left <= 0:
        return None
    return {"left": left, "reason": d.get("reason", "?")}


def snooze_left(now: float | None = None) -> float:
    """Otomatik kaydın yeniden başlaması için kalan süre (sn). 0 = serbest."""
    info = snooze_info(now)
    return info["left"] if info else 0.0


def clear_snooze() -> None:
    SNOOZE.unlink(missing_ok=True)


def cmd_snooze(arg: str | None) -> None:
    """Otomatik kaydı N dakika duraklatır; sürmekte olan kaydı da durdurur."""
    if arg is None:
        info = snooze_info()
        if info:
            print(f"Otomatik kayıt duraklatıldı — {info['left'] / 60:.0f} dakika "
                  f"kaldı ({info['reason']})")
        else:
            print("Otomatik kayıt aktif (duraklatma yok).")
        return

    if arg.strip().lower() in ("off", "kapat", "0", "devam", "iptal"):
        clear_snooze()
        print("Duraklatma kaldırıldı — otomatik kayıt yeniden aktif.")
        return

    mins = float(arg)
    # Asıl amaç "bu konuşma kaydedilmesin": süren kayıt da durmalı, yoksa
    # duraklatma yalnızca bir sonrakini etkiler ve kullanıcı yine elle uğraşır.
    stopped = False
    if read_state() is not None:
        try:
            cmd_stop()
            stopped = True
        except SystemExit:
            pass
    set_snooze(mins * 60, "kullanıcı")
    print(f"\nOtomatik kayıt {mins:.0f} dakika duraklatıldı"
          + (" (süren kayıt durduruldu)" if stopped else "")
          + f".\nKendiliğinden yeniden açılacak — elle açmak için: meet snooze off")
    notify(f"Otomatik kayıt {mins:.0f} dk duraklatıldı",
           "Süre dolunca kendiliğinden yeniden açılacak."
           + (" Süren kayıt durduruldu." if stopped else ""),
           subtitle="Kaydı unutma riski yok", level="info")


def cmd_limit(arg: str | None) -> None:
    """Süren kayda süre sınırı koyar/kaldırır (nadiren gerekir).

    Bu, 'bu konuşma kaydedilmesin' için DEĞİL — onun için `meet snooze`.
    Burası uzun bir kaydın kendiliğinden bitmesini istediğin durum.
    """
    st = read_state()
    if arg is None:
        if st and st.get("stop_at"):
            left = float(st["stop_at"]) - time.time()
            print(f"Sınır: {float(st.get('limit_min') or 0):.0f} dk · "
                  f"kalan {max(0, left) / 60:.0f} dk")
        elif st:
            print("Süren kayıtta sınır yok.")
        else:
            print("Kayıt yok. (Kaydın hiç başlamamasını istiyorsan: meet snooze <dk>)")
        return

    off = arg.strip().lower() in ("off", "kapat", "0", "yok", "sınırsız", "sinirsiz")
    mins = 0.0 if off else float(arg)
    if not st:
        die("Aktif kayıt yok. Kaydın hiç başlamamasını istiyorsan: meet snooze <dk>")

    # Yazmadan hemen önce bir kez daha bak: aradaki pencerede stop gelmiş olabilir
    # ve write_state() durdurulmuş kaydı diriltir.
    st = read_state()
    if st is None:
        die("Kayıt bu sırada durdu, sınır yazılmadı.")
    if off:
        st.pop("stop_at", None)
        st.pop("limit_min", None)
        write_state(st)
        print("Süre sınırı kaldırıldı — kayıt normal şekilde devam ediyor.")
        return
    started = dt.datetime.fromisoformat(st["started_at"]).timestamp()
    st["limit_min"] = mins
    st["stop_at"] = started + mins * 60
    write_state(st)
    left = (st["stop_at"] - time.time()) / 60
    if left <= 0:
        print(f"⚠ {mins:.0f} dakika zaten doldu — guard kaydı birkaç saniye içinde "
              f"durduracak.")
    else:
        print(f"Süre sınırı {mins:.0f} dk (kaydın başlangıcından) — kalan "
              f"{left:.0f} dk.")


def _loopback_selftest(cfg: dict, session: Path) -> None:
    """Loopback zincirini kayıt başında sınar.

    Neden gerekli: BlackHole sanal bir cihaz, hiçbir şey çalmazken TAM olarak
    -180 dBFS veriyor — yani "kimse konuşmuyor" ile "ses hiç BlackHole'a gelmiyor"
    seviyeden ayırt edilemiyor. Sessizliği izleyerek sorunu bulmak yanlış alarm üretiyor.
    Bunun yerine kısa ve kısık bir ton çalıp BlackHole'un onu duyup duymadığına bakıyoruz.
    Kesin, anında ve toplantı boyunca tekrar gerekmiyor.
    """
    st_cfg = cfg.get("guard", {})
    if not st_cfg.get("loopback_selftest", True):
        return
    tone = ROOT_TONE
    if not tone.exists():
        tone.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-y", "-f", "lavfi",
             "-i", "sine=frequency=880:duration=0.35", "-af", "volume=-26dB",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(tone)],
            capture_output=True)
        if r.returncode != 0 or not tone.exists():
            return

    sysw = session / "sys.wav"
    before = _tail_rms_db(sysw, 1.0)
    subprocess.run(["afplay", str(tone)], capture_output=True, timeout=10)
    time.sleep(1.2)
    after = _tail_rms_db(sysw, 1.5)

    if after is None:
        return
    if after > -75:
        print(f"  ✓ loopback doğrulandı (test tonu {after:.0f} dBFS olarak yakalandı)")
        return

    print("\n  ⚠⚠ LOOPBACK ÇALIŞMIYOR — karşı tarafın sesi KAYDEDİLMEYECEK.\n"
          f"     Test tonu BlackHole'a hiç ulaşmadı (önce {before}, sonra {after:.0f} dBFS).\n"
          "     Sırayla kontrol et:\n"
          "       1. Teams > Settings > Devices > Speaker -> 'Meeting Output BT'\n"
          "          (kulaklıksızsan 'Meeting Output SPK')\n"
          "       2. Sistem ses çıkışı bir Multi-Output cihazında mı\n"
          "       3. Multi-Output içinde BlackHole 2ch işaretli mi\n")
    notify("⚠ Loopback çalışmıyor",
           "Teams > Settings > Devices > Speaker -> Meeting Output BT",
           subtitle="Karşı tarafın sesi kaydedilmeyecek", level="error")


_SILENT_STREAK: dict[str, int] = {"sys": 0, "mic": 0}
_WARNED: set[str] = set()
_MIC_WAS_LIVE = {"v": False}
_MIC_DEAD: dict = {"strikes": 0, "restarts": 0, "times": []}


def _recover_mic(cfg: dict, st: dict, session: Path, stamp: str) -> None:
    """Mikrofon dijital sessizliğe düştüyse akışı aynı cihazla yeniden kurar.

    Bluetooth kulaklıkta HFP profili, mikrofonu tutan başka bir uygulama (Teams)
    onu bıraktığında sökülüyor ve macOS A2DP'ye dönüyor. Bizim ffmpeg akışımız bu
    yeniden pazarlıktan sağ çıkmıyor: cihaz açık kalıyor ama sıfır örnek geliyor.
    Ölçüldü — bir Teams test çağrısında mikrofon 30. saniyede -94 dBFS'e düştü ve
    kaydın kalan 24 saniyesi boş kaldı.

    Eşik -80 dBFS: sessiz oda -50'lerde, dijital sessizlik -94'te. İkisi karışmaz.
    """
    g = cfg.get("guard", {})
    if not g.get("recover_mic", True):
        return
    # Kota SAYIYA değil HIZA bakmalı. Ölçüldü (2026-09-09, 68 dk toplantı):
    # rotasyonlar toplantıya yayılıyor, 11:13–12:09 arası 15'in hepsi tükendi ve
    # kayıt 12:18'e kadar sürdüğü için son 9 dakika korumasız kaldı. Sabit toplam
    # sayı, uzun toplantıyı cezalandırıyor. Amaç kaçak döngüyü durdurmak: dakikada
    # bir taneden fazlası anormal, saatlerce süren toplantıda 20 tanesi normal.
    max_restarts = int(g.get("max_mic_restarts", 15))
    window_s = float(g.get("restart_window_s", 300))
    burst = int(g.get("max_restarts_per_window", 6))
    now = time.time()
    _MIC_DEAD["times"] = [t for t in _MIC_DEAD["times"] if now - t < window_s]
    if len(_MIC_DEAD["times"]) >= burst:
        if "mic_burst" not in _WARNED:
            _WARNED.add("mic_burst")
            print(f"[guard] mikrofon {window_s/60:.0f} dakikada {burst} kez koptu — "
                  f"kaçak döngü sayılıyor, rotasyon duraklatıldı", flush=True)
            notify("⚠ Mikrofon sürekli kopuyor",
                   "Kulaklık yerine MacBook mikrofonuna geçmeyi düşün.",
                   subtitle=f"{window_s/60:.0f} dk içinde {burst} kopma", level="error")
        return
    _WARNED.discard("mic_burst")
    if _MIC_DEAD["restarts"] >= max_restarts:
        return

    wav = session / f"mic.part{st['mic']['part']:02d}.wav"
    lvl = _tail_rms_db(wav, 8)
    if lvl is None:
        return
    if lvl >= float(g.get("mic_dead_db", -80)):
        _MIC_DEAD["strikes"] = 0
        return

    _MIC_DEAD["strikes"] += 1
    if _MIC_DEAD["strikes"] < int(g.get("mic_dead_strikes", 3)):
        return

    _MIC_DEAD["strikes"] = 0
    _MIC_DEAD["restarts"] += 1
    _MIC_DEAD["times"].append(now)
    try:
        name, idx = _pick_mic(cfg)
    except SystemExit:
        return
    print(f"[guard {stamp}] ⚠ mikrofon dijital sessizlikte ({lvl:.0f} dBFS) — "
          f"akış yeniden kuruluyor ({_MIC_DEAD['restarts']}/{max_restarts})", flush=True)
    _rotate_mic(cfg, st, session, name, idx)
    if _MIC_DEAD["restarts"] >= max_restarts:
        notify("⚠ Mikrofon kopma sınırına ulaşıldı",
               "Kaydın kalanı korumasız. Kulaklık yerine MacBook mikrofonu daha güvenli.",
               subtitle=f"{max_restarts} kez yeniden kuruldu", level="error")


def _tail_rms_db(wav: Path, seconds: float, sr: int = 16000) -> float | None:
    """Büyümekte olan bir WAV'ın son N saniyesinin RMS'i.

    ffprobe kullanamayız çünkü dosya henüz kapanmadı ve başlıktaki süre yanlış.
    16-bit mono PCM olduğunu bildiğimiz için ham baytları sondan okuyoruz.
    """
    import struct
    want = int(seconds * sr) * 2
    try:
        size = wav.stat().st_size
        if size < 44 + want // 4:
            return None
        with open(wav, "rb") as fh:
            fh.seek(max(44, size - want))
            raw = fh.read()
    except OSError:
        return None
    n = len(raw) // 2
    if n < sr:
        return None
    vals = struct.unpack(f"<{n}h", raw[:n * 2])
    acc = 0.0
    for v in vals:
        acc += (v / 32768.0) ** 2
    rms = (acc / n) ** 0.5
    return 20 * __import__("math").log10(max(rms, 1e-9))


def _watch_silence(cfg: dict, st: dict, session: Path, stamp: str) -> None:
    """Bir kanal ANLAMLI şekilde uzun süre sessizse bir kez uyarır.

    Dikkat: BlackHole sanal bir cihaz. Hiçbir şey çalmazken TAM -180 dBFS veriyor,
    yani "karşı taraf susuyor" ile "ses BlackHole'a hiç gelmiyor" seviye bakarak
    ayırt edilemez. Kayıt başındaki loopback self-test asıl teşhis aracıdır; burası
    sadece toplantı ortasında zincir kopan durumlar için geniş bir emniyet ağı.
    Bu yüzden eşikler dakika mertebesinde ve uyarı toplantı başına bir kez.

    Ek koşul: mikrofon kanalı en az bir kez ses görmüş olmalı — yani toplantı gerçekten
    başlamış olmalı. Sen konuşmuyorken karşı tarafın da susması normaldir.
    """
    g = cfg.get("guard", {})
    if not g.get("watch_silence", True):
        return
    poll = float(g.get("poll_s", 4))
    started = dt.datetime.fromisoformat(st["started_at"])
    if (dt.datetime.now() - started).total_seconds() < float(g.get("silence_grace_s", 240)):
        return
    thresh = float(g.get("silence_db", -70))
    need = max(1, int(float(g.get("silence_window_s", 180)) / poll))

    mic_wav = session / f"mic.part{st['mic']['part']:02d}.wav"
    mic_lvl = _tail_rms_db(mic_wav, 20)
    if mic_lvl is not None and mic_lvl > thresh:
        _MIC_WAS_LIVE["v"] = True

    checks = [
        # 2026-09-24: Google Meet toplantısının 46 dakikasında karşı tarafın sesi
        # HİÇ kaydedilmedi (sys ortalaması -71 dBFS). Meet sesi Multi-Output'a
        # değil doğrudan kulaklığa gönderiyordu. Bu uyarı o gün çalışamadı çünkü
        # kayıtlar 41 saniyede kesiliyordu ve silence_grace_s (240 sn) hiç dolmadı.
        # İpucu artık uygulamaya özel değil — Teams, Meet, Zoom hepsinde aynı ayar.
        ("sys", session / "sys.wav", _MIC_WAS_LIVE["v"],
         "Karşı taraf KAYDEDİLMİYOR",
         "Toplantı uygulamasının hoparlör ayarını 'Meeting Output BT/SPK' yap "
         "(Teams: Settings > Devices · Meet/Zoom: ayarlar > Ses > Hoparlör)."),
        ("mic", mic_wav, True,
         "Senin mikrofonun veri göndermiyor",
         "Bluetooth kulaklıkta HFP bağlantısı kopmuş olabilir; kulaklığı çıkarıp tak."),
    ]
    for key, wav, precondition, label, hint in checks:
        lvl = _tail_rms_db(wav, 20)
        if lvl is None or not precondition:
            continue
        if lvl < thresh:
            _SILENT_STREAK[key] += 1
            if _SILENT_STREAK[key] >= need and key not in _WARNED:
                _WARNED.add(key)
                mins = need * poll / 60
                print(f"[guard {stamp}] ⚠ {label} — {mins:.0f} dakikadır sessiz "
                      f"({lvl:.0f} dBFS)\n         {hint}", flush=True)
                notify("⚠ Kayıt sorunu", hint, subtitle=f"{label} ({mins:.0f} dk)",
                       level="error", open_path=session)
        else:
            _SILENT_STREAK[key] = 0


def _rotate_mic(cfg: dict, st: dict, session: Path, name: str, idx: int) -> None:
    """Loopback kaydını kesmeden mikrofon kanalını yeni cihaza taşır.

    KRİTİK: durumu yazmadan önce kaydın hâlâ sürdüğünü DOĞRULA.
    Bir kez şu oldu: `meet stop` durumu sildi, guard aynı döngüde ölmüş mikrofonu
    "dijital sessizlik" sanıp rotasyon yaptı ve write_state() ile durdurulmuş kaydı
    diriltti. İzleyici tekrar "kayıt var" görüp durdurdu, her durdurma bir transkript
    süreci doğurdu — altı süreç aynı toplantıyı işlemeye başladı ve makine kilitlendi.
    """
    if read_state() is None:
        print("[guard] kayıt durdurulmuş, rotasyon iptal", flush=True)
        return
    _stop_pid(st["mic"]["pid"])
    # Eski mikrofon süreci gerçekten ölmediyse yeni akışla aynı cihaz için yarışır.
    # Loopback'e dokunma: sys pid korunur.
    _reap_session_ffmpeg(session, keep={st["sys"]["pid"]})
    n = st["mic"]["part"] + 1
    out = session / f"mic.part{n:02d}.wav"
    log = session / f"mic.part{n:02d}.log"
    proc = _spawn(_mic_cmd(cfg, idx, out), log)
    time.sleep(2.5)
    if proc.poll() is not None:
        print(f"[guard] yeni mikrofon açılamadı, eski cihazla devam edilemiyor "
              f"(parça {n} boş kalacak)", flush=True)
    # Yazmadan hemen önce bir kez daha bak — bu iki nokta arasında stop gelmiş olabilir.
    if read_state() is None:
        print("[guard] kayıt bu sırada durdu, yeni parça bırakılıyor", flush=True)
        _stop_pid(proc.pid)
        return
    st["mic"] = {"pid": proc.pid, "log": log.name, "device": name, "part": n,
                 "rank": _mic_rank(cfg, name)}
    st["parts"].append({"n": n, "file": out.name, "log": log.name, "device": name})
    write_state(st)
    print(f"[guard] parça {n} başladı: {name}", flush=True)
    notify("Mikrofon değişti", f"Kayıt {name} üzerinden devam ediyor",
           subtitle=f"Parça {n} — toplantı kesilmedi", level="info")


# ==========================================================================
# stop
# ==========================================================================

class _Tee:
    """Yazılanı hem ekrana hem dosyaya geçirir, her satırda flush eder."""

    def __init__(self, path: Path, stream) -> None:
        self.f = open(path, "a", encoding="utf-8")
        self.s = stream

    def write(self, b: str) -> int:
        try:
            self.s.write(b)
        except Exception:  # noqa: BLE001 — ekran yoksa dosya yine yazılsın
            pass
        self.f.write(b)
        self.f.flush()
        return len(b)

    def flush(self) -> None:
        try:
            self.s.flush()
        except Exception:  # noqa: BLE001
            pass
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def cmd_stop() -> None:
    """Kaydı durdurur ve NE OLDUĞUNU toplantı klasörüne yazar.

    `stop.log` 2026-10-30'da eklendi. Sebep: iki toplantıda transkript hiç
    başlamamıştı ve NEDENİ ÖĞRENİLEMEDİ — çünkü bu fonksiyonun çıktısı hiçbir
    yere gitmiyordu. Otomatik izleyici `capture_output=True` ile çağırıp sonucu
    atıyor, guard ise DEVNULL'a yazıyor. Yani durdurma adımı sessiz bir kutuydu:
    merge başarısız mı oldu, transkript mi başlatılamadı, bir istisna mı atıldı —
    hiçbiri görünmüyordu.

    İZ BIRAKMAYAN OTOMASYON HATA AYIKLANAMAZ. Durdurma yolunda yeni kod yazarken
    `print` kullan; bu sarmalayıcı onu kalıcı hâle getiriyor.
    """
    cfg = load_config()
    st = read_state()
    if st is None:
        die("Aktif kayıt yok.")
    session = Path(st["session_dir"])
    tee = _Tee(session / "stop.log", sys.stdout)
    try:
        with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
            print(f"\n=== meet stop · {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===")
            _stop_body(cfg, st, session)
    except Exception:  # noqa: BLE001
        tee.write("\n!!! DURDURMA SIRASINDA İSTİSNA\n" + traceback.format_exc())
        notify("⚠ Kayıt durdurulurken hata",
               "Ses dosyaları yerinde olabilir ama transkript başlamamış olabilir.",
               subtitle=f"Ayrıntı: {session.name}/stop.log", level="error",
               open_path=session)
        raise
    finally:
        tee.close()


def _stop_body(cfg: dict, st: dict, session: Path) -> None:

    # İkisine de önce sinyali gönder, SONRA bekle. Sırayla durdurursak ikinci süreç
    # birincinin kapanışını beklediği kadar fazladan kaydediyor (ölçüldü: 3 sn).
    for pid in (st["mic"]["pid"], st["sys"]["pid"]):
        if _alive(pid):
            os.kill(pid, signal.SIGINT)
    _stop_pid(st["mic"]["pid"])
    _stop_pid(st["sys"]["pid"])
    parts = st["parts"]
    prev_out = st.get("prev_output")

    # Guard'ı ÖNCE ve AÇIKÇA öldür, sonra durumu sil. Guard'ın kendi kendine
    # fark etmesini beklemek yetmiyor: aradaki 4 saniyelik pencerede ölmüş mikrofonu
    # dijital sessizlik sanıp rotasyon yapıyor ve write_state() ile kaydı diriltiyor.
    if (gpid := st.get("guard_pid")):
        _stop_pid(gpid, sig=signal.SIGTERM, wait=5)
    subprocess.run(["pkill", "-f", "record.py guard"], capture_output=True)
    time.sleep(0.5)
    write_state(None)

    # Durum dosyası yalnızca SON mic pid'ini bilir. Rotasyonda geride kalan ya da
    # guard çökünce sahipsiz kalan ffmpeg'ler burada toplanır; yoksa mikrofonu
    # açık tutup sonraki toplantıları bozuyorlar.
    if (n := _reap_session_ffmpeg(session)):
        print(f"  {n} sahipsiz ffmpeg süreci toplandı")

    # Çıkışı geri al. prev_out'a KÖRÜ KÖRÜNE dönme: bir kez Multi-Output'ta takılı
    # kalındıysa prev_out da Multi-Output olur ve cihaz oradan bir daha çıkamaz.
    if cfg["audio"].get("restore_output", True) and audiodev.available():
        target, why = audiodev.sane_idle_output(cfg, prev_out)
        cur = audiodev.current_output()
        if target and target != cur:
            if audiodev.set_output(target):
                print(f"  çıkış geri alındı: {cur} -> {target}  ({why})")
            else:
                print(f"  ⚠ çıkış '{target}' olarak geri alınamadı, {cur} kaldı")
        elif not target:
            print(f"  ⚠ çıkış geri alınamadı: {why}")

    _assemble_mic(session, parts)

    print(f"\n■ KAYIT DURDU\n  {session}")
    for f in ("mic.wav", "sys.wav"):
        p = session / f
        if not p.exists() or p.stat().st_size < 1024:
            print(f"  ⚠ {f} oluşmadı ya da boş")
            continue
        lvl = _mean_db(p)
        flag = "  ⚠ SESSİZ" if lvl < -60 else ""
        print(f"  {f}: {_dur(p):6.1f} sn, {p.stat().st_size/1e6:5.1f} MB, "
              f"ortalama {lvl:6.1f} dBFS{flag}")

    if len(parts) > 1:
        print(f"  mikrofon {len(parts)} parçada kaydedildi "
              f"({', '.join(p['device'] for p in parts)}) ve birleştirildi")

    mins = int(_dur(session / "sys.wav") // 60)
    notify("Kayıt durdu", f"{mins} dakika — transkript hazırlanıyor",
           subtitle=session.name, level="info", open_path=session)

    if cfg.get("post_process", {}).get("auto_transcribe", True):
        _start_transcription(session)
    else:
        print(f"  İşlemek için: meet transcribe \"{session}\"")


def _transcription_running(session: Path) -> int | None:
    """Bu toplantı için zaten çalışan bir transkript süreci varsa pid'ini döner."""
    r = subprocess.run(["pgrep", "-f", f"pipeline.py --transcribe {session}"],
                       capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.split() if p.strip().isdigit()]
    return pids[0] if pids else None


def _any_transcription_running() -> bool:
    """Herhangi bir toplantının transkripti sürüyor mu (hangisi olduğu fark etmez)."""
    r = subprocess.run(["pgrep", "-f", "pipeline.py --transcribe"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def _start_transcription(session: Path) -> None:
    """Transkripti arka planda başlatır.

    Aynı toplantı için ikinci bir süreç ASLA başlatılmaz. Bir kez altı kopya birden
    çalıştı: her biri ~3 GB model yükledi, GPU ve bellek için yarıştılar, 50 dakikalık
    bir kayıt 2.5 saatlik tahmine çıktı ve altısı da aynı progress.json'a yazdığı için
    ilerleme göstergesi de anlamsızlaştı.
    """
    if (pid := _transcription_running(session)) is not None:
        print(f"  Bu toplantı için transkript zaten çalışıyor (pid {pid}), "
              f"ikinci süreç başlatılmadı.")
        return
    meet = Path(__file__).resolve().parent.parent / "bin" / "meet"
    log = open(session / "process.log", "wb")
    proc = subprocess.Popen([str(meet), "transcribe", str(session)],
                            stdin=subprocess.DEVNULL,
                            stdout=log, stderr=log, start_new_session=True)
    print(f"\n  Transkript arka planda başladı (pid {proc.pid}).\n"
          f"  İlerleme: tail -f \"{session / 'process.log'}\"")

    # BAŞLADIĞINI DOĞRULA. İki toplantıda transkript hiç çıkmadı ve bunu fark
    # etmek günler sürdü; sessizce başlamayan bir süreç, hiç denenmemişten
    # ayırt edilemiyordu. Birkaç saniye bekleyip gerçekten ayakta mı diye bak.
    time.sleep(4)
    if proc.poll() is not None or _transcription_running(session) is None:
        tail = ""
        try:
            tail = (session / "process.log").read_text(
                encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            tail = " | ".join(tail)
        except OSError:
            pass
        print(f"  ⚠ TRANSKRİPT BAŞLAMADI (çıkış kodu {proc.poll()}). {tail}")
        notify("⚠ Transkript başlamadı", "Menüden 'Transkript Çıkar' ile elle "
               "başlatabilirsin.", subtitle=session.name, level="error",
               open_path=session)


def _assemble_mic(session: Path, parts: list[dict]) -> None:
    """Mikrofon parçalarını mutlak zaman damgalarına göre tek dosyada birleştirir.

    Referans saat sistem kanalının başlangıç epoch'u; her parça kendi ffmpeg logundaki
    epoch'a göre gecikmeyle yerleştirilir, aradaki boşluklar sessizlik olur.
    """
    out = session / "mic.wav"
    live = [p for p in parts if (session / p["file"]).exists()
            and (session / p["file"]).stat().st_size > 1024]
    if not live:
        print("  ⚠ hiçbir mikrofon parçası oluşmadı")
        return
    ref = _stream_epoch(session / "sys.ffmpeg.log")
    delays = []
    for p in live:
        ep = _stream_epoch(session / p["log"])
        d = max(0.0, (ep - ref)) if (ep and ref) else 0.0
        delays.append(int(d * 1000))

    if len(live) == 1:
        # Tek parça: gecikme uygulanmadan olduğu gibi mic.wav olur. Yerleşim
        # haritası yine yazılmalı — timeline.py mikrofon kanalının zaman
        # düzeltmesini buradan kuruyor.
        _write_parts_map(session, live, delays, ref)
        (session / live[0]["file"]).replace(out)
        return

    inputs, filters, labels = [], [], []
    for i, (p, dly) in enumerate(zip(live, delays)):
        inputs += ["-i", str(session / p["file"])]
        filters.append(f"[{i}:a]adelay={dly}|{dly}[a{i}]")
        labels.append(f"[a{i}]")
    fc = ";".join(filters) + ";" + "".join(labels) + \
         f"amix=inputs={len(live)}:normalize=0:dropout_transition=0[out]"

    r = subprocess.run([FFMPEG, "-hide_banner", "-y", *inputs,
                        "-filter_complex", fc, "-map", "[out]",
                        "-c:a", "pcm_s16le", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        print("  ⚠ parça birleştirme başarısız, ilk parça kullanılıyor:\n" + r.stderr[-800:])
        (session / live[0]["file"]).replace(out)
        return

    print(f"  {len(live)} mikrofon parçası birleştirildi "
          f"(gecikmeler: {', '.join(f'{d/1000:.1f}s' for d in delays)})")
    _write_parts_map(session, live, delays, ref)
    _cleanup_parts(session, parts, out)


def _write_parts_map(session: Path, live: list[dict], delays: list[int],
                     ref: float | None) -> None:
    """Parçaların mic.wav içindeki yerleşimini kalıcı olarak yaz.

    `timeline.py` mikrofon kanalının zaman haritasını bu yerleşimden kuruyor.
    Parça loglarından yeniden türetmek yetmiyor: `meet audit --fix` eski/büyük
    logları siliyor ve geriye tek bir parça logu kalınca harita sessizce yanlış
    çıkıyor (ölçüldü: 17 parçalı bir kayıtta mic haritası 63 dakika kaydı).
    """
    try:
        (session / "mic.parts.json").write_text(json.dumps({
            "ref_epoch": ref,
            "parts": [{"file": p["file"], "log": p["log"],
                       "delay_s": round(d / 1000.0, 3),
                       "dur_s": round(_dur(session / p["file"]), 3)}
                      for p, d in zip(live, delays)],
        }, indent=2), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        print(f"  ⚠ parça haritası yazılamadı: {exc}")


def _cleanup_parts(session: Path, parts: list[dict], merged: Path) -> None:
    """Birleştirme doğrulandıysa parçaları sil.

    Parçalar birikiyordu: 30 toplantıda 203 dosya / 2.7 GB, hepsi mic.wav'ın içinde
    zaten var olan sesin kopyası. Silmeden önce birleşik dosyanın parçaların toplam
    süresini kapsadığını doğruluyoruz — kapsamıyorsa dokunmuyoruz, çünkü o durumda
    parçalar tek kurtarma yolu.
    """
    from common import load_config
    if not load_config().get("post_process", {}).get("delete_mic_parts", True):
        return
    try:
        psum = sum(_dur(session / p["file"]) for p in parts
                   if (session / p["file"]).exists())
        if _dur(merged) < psum - 5:
            print("  ⚠ birleşik dosya parçalardan kısa — parçalar SİLİNMEDİ")
            return
        freed = 0
        for p in parts:
            f = session / p["file"]
            if f.exists():
                freed += f.stat().st_size
                f.unlink()
            lg = session / p["log"]
            if lg.exists():
                lg.unlink()
        if freed:
            print(f"  {len(parts)} parça silindi ({freed/1e6:.0f} MB)")
    except OSError as exc:
        print(f"  ⚠ parça temizliği yapılamadı: {exc}")


# ==========================================================================
# durum / cihazlar
# ==========================================================================

def cmd_status() -> None:
    st = read_state()
    if st is None:
        print("Kayıt yok.")
        return
    if not (_alive(st["sys"]["pid"]) or _alive(st["mic"]["pid"])):
        write_state(None)
        print("Kayıt yok (süreçler ölmüş, durum temizlendi).")
        return
    el = dt.datetime.now() - dt.datetime.fromisoformat(st["started_at"])
    s = int(el.total_seconds())
    print(f"● Kayıt sürüyor — {s//60} dk {s%60} sn\n"
          f"  {st['session_dir']}\n"
          f"  mikrofon: {st['mic']['device']} (parça {st['mic']['part']})\n"
          f"  sistem  : {st['sys']['device']}\n"
          f"  çıkış   : {audiodev.current_output() if audiodev.available() else '?'}")


def cmd_lang(code: str | None) -> None:
    """Kayıt sırasında dil işareti koyar: meet lang en / tr / auto

    Kaydı BÖLMEZ. Sadece toplantı klasörüne zaman damgalı bir işaret yazar; transkript
    aşaması o andan sonraki konuşma pencerelerini bu dille çözer. Aynı ses dosyası,
    farklı aralıklar farklı dil.
    """
    st = read_state()
    if st is None:
        die("Aktif kayıt yok. Dil işareti sadece kayıt sürerken konabilir.\n"
            "  Kayıt bittiyse: config.yaml -> transcribe.language ayarla ya da\n"
            "  meet transcribe --language en \"<klasör>\" çalıştır.")
    if not code:
        die("kullanım: meet lang {tr|en|auto}")
    code = code.lower()

    session = Path(st["session_dir"])
    started = dt.datetime.fromisoformat(st["started_at"])
    t = (dt.datetime.now() - started).total_seconds()

    f = session / "lang_markers.json"
    marks = json.loads(f.read_text()) if f.exists() else []
    if code == "auto":
        marks = [m for m in marks if m["t"] < t - 0.5]
        marks.append({"t": round(t, 2), "lang": None})
        marks = [m for m in marks if m["lang"]]
        f.write_text(json.dumps(marks, indent=2))
        print(f"  [{int(t)//60}:{int(t)%60:02d}] dil işaretleri kaldırıldı, "
              f"otomatik algılamaya dönüldü")
        return

    marks.append({"t": round(t, 2), "lang": code})
    f.write_text(json.dumps(marks, indent=2))
    print(f"  [{int(t)//60}:{int(t)%60:02d}] dil işareti kondu: {code}\n"
          f"  Bu andan sonraki konuşma {code} olarak çözülecek. Kayıt kesilmedi.")


def cmd_devices() -> None:
    cfg = load_config()
    print("Ses GİRİŞ cihazları (avfoundation):\n")
    for idx, name in list_audio_devices():
        ch = probe_channels(idx, cfg["audio"]["sample_rate"])
        print(f"  [{idx}] {name}  ({ch} kanal)" if ch else f"  [{idx}] {name}")
    if audiodev.available():
        print("\nSes ÇIKIŞ cihazları:\n")
        cur = audiodev.current_output()
        for name in audiodev.list_outputs():
            print(f"  {'●' if name == cur else ' '} {name}")
        target, why = audiodev.pick_output_profile(cfg)
        print(f"\nKayıt sırasında kullanılacak çıkış: {target or 'BELİRLENEMEDİ'}  ({why})")
    prio = cfg["audio"].get("mic_priority") or []
    print(f"\nMikrofon önceliği: {' > '.join(prio)}")
    try:
        name, idx = _pick_mic(cfg)
        print(f"Şu an seçilecek: [{idx}] {name}")
    except SystemExit:
        print("Şu an seçilecek: HİÇBİRİ BAĞLI DEĞİL")


def _start_from_args(args: list[str]) -> None:
    """`start [başlık] [--for <dk>]` — başlık isteğe bağlı, sıra önemsiz."""
    rest, limit = [], None
    i = 1
    while i < len(args):
        a = args[i]
        if a in ("--for", "--sure", "--süre"):
            if i + 1 >= len(args):
                die("--for için dakika ver (örn: --for 30)")
            try:
                limit = float(args[i + 1])
            except ValueError:
                die(f"--for sayı bekliyor, '{args[i + 1]}' geldi")
            i += 2
            continue
        rest.append(a)
        i += 1
    cmd_start(rest[0] if rest else None, limit)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        die("kullanım: record.py {start|stop|status|devices|guard|lang|limit|snooze} [başlık]")
    {"start": lambda: _start_from_args(args),
     "stop": cmd_stop, "status": cmd_status, "devices": cmd_devices,
     "guard": cmd_guard,
     "lang": lambda: cmd_lang(args[1] if len(args) > 1 else None),
     "limit": lambda: cmd_limit(args[1] if len(args) > 1 else None),
     "snooze": lambda: cmd_snooze(args[1] if len(args) > 1 else None),
     }.get(args[0], lambda: die(f"bilinmeyen komut: {args[0]}"))()
