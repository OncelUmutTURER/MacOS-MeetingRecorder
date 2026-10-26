"""Menü çubuğu uygulaması — pipeline'ın asıl arayüzü.

Terminal gerekmeden: kayıt başlat/durdur, dil işareti koy, mikrofon kontrolü yap,
son toplantıyı işle, cihaz durumunu gör.

Başlatma:  meet menubar        (elle)
           meet install-agent  (açılışta otomatik başlasın)
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading
from pathlib import Path

import rumps

import audiodev
import audiowatch
import progress as P
import record as R
import update as U
from common import (find_output, has_output, load_config, meetings_dir,
                    read_state)
from notify import notify

ROOT = Path(__file__).resolve().parent.parent
MEET = str(ROOT / "bin" / "meet")
LOCK = ROOT / ".menubar.lock"

IDLE, REC = "○", "●"
# İşlem sürerken dönen çeyrek daireler; yüzde bilinmiyorsa canlılık göstergesi olur.
SPIN = ["◜", "◝", "◞", "◟"]
LANGS = [("Otomatik algıla", "auto"), ("Türkçe", "tr"), ("İngilizce", "en")]
# Otomatik kaydı GEÇİCİ duraklatma. Kalıcı anahtarı kapatmak çözüm değildi:
# "tekrar açmayı unuturum diye korkuyorum". Süre dolunca kendiliğinden açılır.
SNOOZES = [("15 dakika", 15), ("30 dakika", 30), ("60 dakika", 60)]
RESUME = "Şimdi Devam Ettir"


def hide_from_dock() -> None:
    """Uygulamayı Dock'tan ve ⌘-Tab listesinden kaldır, sadece menü çubuğunda yaşasın.

    rumps çıplak script olarak çalıştırıldığında Python normal bir uygulama gibi
    görünüyor. Accessory politikası bunu düzeltir; .app paketlemeye gerek yok.
    """
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)
    except Exception:  # noqa: BLE001 — ikon görünmesi çalışmamasından iyidir
        pass


def acquire_single_instance() -> object | None:
    """Aynı anda tek menü çubuğu kopyası çalışsın.

    Bir kez iki kopya birden çalıştı (biri launchd'den, biri elle başlatılmış) ve
    Dock'ta iki ikon göründü. flock süreç ölünce kendiliğinden serbest kalır, bayat
    pid dosyası derdi yok.
    """
    import fcntl
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh          # referansı tut, yoksa GC kilidi bırakır


class MeetingApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(IDLE, quit_button=None)
        self.cfg = load_config()
        self.busy = False
        self.lang_choice = "auto"

        self.i_toggle = rumps.MenuItem("Kaydı Başlat", callback=self.toggle)
        self.i_lang = rumps.MenuItem("Toplantı Dili")
        for label, code in LANGS:
            self.i_lang.add(rumps.MenuItem(label, callback=self.set_lang))
        self.i_snooze = rumps.MenuItem("Otomatik Kaydı Duraklat")
        for label, mins in SNOOZES:
            self.i_snooze.add(rumps.MenuItem(label, callback=self.set_snooze))
        self.i_snooze.add(rumps.separator)
        self.i_snooze.add(rumps.MenuItem(RESUME, callback=self.resume_auto))
        self.i_auto = rumps.MenuItem("Toplantıda Otomatik Kaydet", callback=self.toggle_auto)
        # Alt menü: 31 toplantıyı topluca işlemek istemiyorsun; son toplantı, tümü
        # ve tek tek seçim ayrı seçenekler.
        self.i_actions = rumps.MenuItem("Action Item Raporu")
        self._menu_index: dict[str, Path] = {}
        self._menu_sig: str = ""
        self.i_mic = rumps.MenuItem("Mikrofon Kontrolü", callback=self.miccheck)
        # "İşle" belirsizdi: kullanıcı action item de bekliyordu ama bu sadece
        # transkript üretiyor. Adı ne yaptığını söylesin.
        self.i_process = rumps.MenuItem("Son Toplantının Transkriptini Çıkar",
                                        callback=self.process)
        self.i_folder = rumps.MenuItem("Meetings Klasörünü Aç", callback=self.open_folder)
        self.i_update = rumps.MenuItem(f"Sürüm {U.current_version()}",
                                       callback=self.check_update)
        self.i_stat_mic = rumps.MenuItem("")
        self.i_stat_out = rumps.MenuItem("")

        self.menu = [
            self.i_stat_mic, self.i_stat_out,
            None,
            self.i_toggle,
            self.i_lang,
            self.i_auto,
            self.i_snooze,
            None,
            self.i_mic,
            self.i_process,
            self.i_actions,
            self.i_folder,
            None,
            self.i_update,
            rumps.MenuItem("Çık", callback=rumps.quit_application),
        ]
        self._refresh_lang_marks()
        self._refresh_snooze_item()
        self._update_info: dict | None = None
        self._update_check_background()
        self._watcher: threading.Thread | None = None
        self.i_auto.state = 1 if audiowatch.autostart_on(self.cfg) else 0
        if self.i_auto.state:
            self._start_watcher()

    # ------------------------------------------------------------------
    # durum döngüsü
    # ------------------------------------------------------------------
    @rumps.timer(2)
    def tick(self, _) -> None:
        self._spin = (getattr(self, "_spin", 0) + 1) % len(SPIN)

        # KAYIT HER ZAMAN ÖNCE GELİR. Arka arkaya iki toplantıya girildiğinde
        # öncekinin transkripti çalışırken yenisi kaydediliyor olabiliyor; eskiden
        # burada yalnızca ilerleme gösterilip `return` ediliyordu ve kullanıcı
        # "kaydetmiyor" sanıyordu. İki durum aynı anda gösterilir:  ● 3:41 ◜42%
        st = read_state()
        prog = P.latest(meetings_dir(self.cfg))

        # Dil seçimi TOPLANTIYA özeldir, kalıcı ayar değil. Eskiden elle "Türkçe"
        # seçilince bir sonraki toplantı da ona kilitli başlıyordu; kullanıcı bunu
        # bildirdi. Kayıt biter bitmez otomatik algılamaya dön.
        was = getattr(self, "_was_recording", False)
        if was and not st:
            if self.lang_choice != "auto":
                prev = self.lang_choice
                self.lang_choice = "auto"
                self._refresh_lang_marks()
                notify("Dil otomatiğe döndü",
                       f"'{prev}' seçimi bu toplantıya özeldi.",
                       subtitle="Sonraki kayıt otomatik algılama ile başlayacak",
                       level="quiet")
        self._was_recording = bool(st)
        self._refresh_snooze_item()
        self._refresh_update_item()

        rec_part = ""
        if st:
            el = int((dt.datetime.now()
                      - dt.datetime.fromisoformat(st["started_at"])).total_seconds())
            rec_part = f"{REC} {el // 60}:{el % 60:02d}"
            if st.get("stop_at"):   # meet limit ile konmuş nadir süre sınırı
                left = max(0, float(st["stop_at"]) - dt.datetime.now().timestamp())
                rec_part += f" ⏱{int(left // 60) + (1 if left % 60 else 0)}dk"
        prog_part = f"{SPIN[self._spin]}{prog['pct']:.0f}%" if prog else ""

        if st or prog:
            self.title = " ".join(x for x in (rec_part, prog_part) if x)
        elif self.busy:
            self.title = SPIN[self._spin]
            return
        elif (snz := R.snooze_info()):
            # Duraklatma MENÜ ÇUBUĞUNDAN görünmeli. Kullanıcı kalıcı anahtarı
            # kapatmaktan "unuturum" diye çekindiği için süreli yaptık; görünmezse
            # aynı belirsizlik sürer.
            self.title = f"⏸{snz['left'] / 60:.0f}"
        else:
            self.title = IDLE

        # Kayıt sürerken durdurma her zaman açık kalmalı — işleme sürüyor diye
        # kullanıcının kaydı durduramaması kabul edilemez.
        self.i_toggle.set_callback(self.toggle if st or not prog else None)
        self.i_toggle.title = "Kaydı Durdur" if st else "Kaydı Başlat"

        if prog:
            eta = P.human_eta(prog.get("eta_s"))
            detail = ("   " + (prog.get("detail") or "")
                      + (f"   ·   kalan {eta}" if eta else ""))
            if st:
                # İkisi birden: mikrofon satırı kaydı, çıkış satırı işlemeyi gösterir.
                self.i_stat_mic.title = (f"● KAYITTA — {st['mic']['device']}"
                                         + (f"  (parça {st['mic']['part']})"
                                            if st["mic"]["part"] > 1 else ""))
                self.i_stat_out.title = f"Aynı anda işleniyor: {prog['label']}{detail}"
            else:
                self.i_stat_mic.title = f"İşleniyor: {prog['label']}"
                self.i_stat_out.title = detail
            self.i_process.set_callback(None)
            self.i_mic.set_callback(None)
            self._refresh_actions_item()
            return

        if st:
            self.i_toggle.title = "Kaydı Durdur"
            self.i_stat_mic.title = f"Mikrofon: {st['mic']['device']}"
            if st["mic"]["part"] > 1:
                self.i_stat_mic.title += f"  (parça {st['mic']['part']})"
            self.i_process.set_callback(None)
            self.i_mic.set_callback(None)
        else:
            self.i_toggle.title = "Kaydı Başlat"
            self.i_process.set_callback(self.process)
            self.i_mic.set_callback(self.miccheck)
            try:
                prio = self.cfg["audio"].get("mic_priority") or []
                avail = next((p for p in prio if audiodev.input_exists(p)), None)
                self.i_stat_mic.title = f"Mikrofon: {avail or 'BULUNAMADI'}"
            except Exception:  # noqa: BLE001
                self.i_stat_mic.title = "Mikrofon: ?"
        if (snz := R.snooze_info()):
            self.i_stat_out.title = (f"⏸ Otomatik kayıt duraklatıldı — "
                                     f"{snz['left'] / 60:.0f} dk kaldı")
        else:
            try:
                self.i_stat_out.title = f"Çıkış: {audiodev.current_output() or '?'}"
            except Exception:  # noqa: BLE001
                self.i_stat_out.title = "Çıkış: ?"
        self._refresh_actions_item()

    MAX_LISTED = 12

    def _refresh_actions_item(self) -> None:
        """Alt menüyü bekleyen toplantılara göre yeniden kurar.

        Sadece bekleyen küme değiştiğinde yeniden kurulur — her 2 saniyede menüyü
        yeniden inşa etmek hem gereksiz hem de menü açıkken sorun çıkarır.
        """
        try:
            pending = self._pending_meetings()
        except Exception:  # noqa: BLE001
            return
        sig = "|".join(p.name for p in pending)
        if sig == self._menu_sig:
            return
        self._menu_sig = sig

        self.i_actions.title = (f"Action Item Komutu Hazırla  ({len(pending)} bekliyor)"
                                if pending else "Action Item Komutu Hazırla  (bekleyen yok)")
        # rumps'ta hiç çocuğu olmayan bir MenuItem'ın alt menüsü henüz yaratılmamış
        # oluyor ve clear() AttributeError atıyor. İlk kurulumda temizlenecek bir şey
        # zaten yok.
        try:
            self.i_actions.clear()
        except AttributeError:
            pass
        self._menu_index = {}
        if not pending:
            self.i_actions.add(rumps.MenuItem("Bekleyen toplantı yok"))
            return

        # Başlıklar ne YAPTIĞINI söylesin: bu menü raporu üretmiyor, komutu hazırlıyor.
        last = rumps.MenuItem(f"Son Toplantı — {self._label(pending[-1])}",
                              callback=self.action_last)
        self.i_actions.add(last)
        self.i_actions.add(rumps.MenuItem(
            f"Bekleyen Tümü ({len(pending)}) — 5'erli turlar hâlinde",
            callback=self.action_all))
        self.i_actions.add(rumps.separator)

        # Tek tek seçim — en yeniden eskiye, en fazla MAX_LISTED tane
        for d in reversed(pending[-self.MAX_LISTED:]):
            title = self._label(d)
            self._menu_index[title] = d
            self.i_actions.add(rumps.MenuItem(title, callback=self.action_one))
        if len(pending) > self.MAX_LISTED:
            self.i_actions.add(rumps.MenuItem(
                f"…ve {len(pending) - self.MAX_LISTED} tane daha eski"))

        self.i_actions.add(rumps.separator)
        self.i_actions.add(rumps.MenuItem("Bekleyenleri Atla…", callback=self.skip_all))

    # ------------------------------------------------------------------
    # eylemler
    # ------------------------------------------------------------------
    def toggle(self, _) -> None:
        if read_state():
            self._run(["stop"], "Kayıt durduruldu")
        else:
            self._run(["start"], "Kayıt başladı")

    def set_lang(self, sender) -> None:
        code = dict((l, c) for l, c in LANGS)[sender.title]
        self.lang_choice = code
        self._refresh_lang_marks()
        if read_state():
            r = subprocess.run([MEET, "lang", code], capture_output=True, text=True)
            if r.returncode == 0:
                notify("Dil işareti kondu",
                       "Bu andan sonraki konuşma bu dilde çözülecek. Kayıt kesilmedi.",
                       subtitle={"auto": "Otomatik algılama", "tr": "Türkçe",
                                 "en": "İngilizce"}[code], level="quiet")
        else:
            notify("Dil tercihi kaydedildi",
                   "Kayıt başlayınca uygulanacak, toplantı bitince otomatiğe dönecek."
                   if code != "auto" else "Otomatik algılamaya dönüldü.",
                   level="quiet")

    def _refresh_lang_marks(self) -> None:
        for label, code in LANGS:
            self.i_lang[label].state = 1 if code == self.lang_choice else 0

    def set_snooze(self, sender) -> None:
        """Otomatik kaydı N dakika duraklat; sürmekte olan kaydı da durdur.

        Kullanıcının ihtiyacı: "bazı konuşmaları kaydetmek istemiyorum, ama
        kaydı durdur demem yeterli olmuyor, kayıt tekrar başlıyor. Otomatik
        kaydet toggle'ını kapatırsam da tekrar açmayı unuturum diye korkuyorum."
        Bu yüzden duraklatma SÜRELİ ve kendiliğinden geri açılıyor.
        """
        mins = dict(SNOOZES)[sender.title]
        self._run_async([MEET, "snooze", str(mins)],
                        f"Otomatik kayıt {mins} dk duraklatıldı")

    def resume_auto(self, _) -> None:
        self._run_async([MEET, "snooze", "off"], "Otomatik kayıt yeniden aktif")
        notify("Otomatik kayıt yeniden aktif", "Duraklatma kaldırıldı.",
               level="quiet")

    def _run_async(self, args: list[str], _label: str) -> None:
        """Komutu arka planda çalıştır — `snooze` kaydı durdurabilir, bu sürüyor."""
        def work() -> None:
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode != 0:
                notify("⚠ Komut başarısız", (r.stderr or r.stdout).strip()[:180],
                       level="error")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # sürüm / güncelleme
    # ------------------------------------------------------------------
    def _update_check_background(self) -> None:
        """Açılışta bir kez, arka planda. İNDİRME YOK, yalnızca haber.

        Ağ çağrısı menü çubuğu döngüsünü ASLA bloklamamalı: kayıt sürerken
        donan bir menü, kaydı durduramamak demek.
        """
        def work() -> None:
            try:
                res = U.check(self.cfg)      # kendi aralığına uyar, kapalıysa None
            except Exception:                # noqa: BLE001 — haber hiç kritik değil
                return
            if res and res.get("newer"):
                self._update_info = res
                notify(f"Yeni sürüm: {res['version']}",
                       "Menü çubuğundan 'Yeni Sürüm Var' maddesine tıkla.",
                       subtitle=f"Şu an {U.current_version()} kullanıyorsun",
                       level="quiet", open_url=res.get("url"))
        threading.Thread(target=work, daemon=True).start()

    def _refresh_update_item(self) -> None:
        info = getattr(self, "_update_info", None)
        if info and info.get("newer"):
            self.i_update.title = f"▸ Yeni Sürüm Var: {info['version']}"
        else:
            self.i_update.title = f"Sürüm {U.current_version()}"

    def check_update(self, _) -> None:
        """Elle kontrol. Kurulumu YAPMAZ — komutu panoya koyar.

        Kendi kendini güncelleyen bir kayıt yazılımı, toplantının ortasında kendi
        kodunu değiştirme riski taşır. Kurulum `git clone` olduğu için güncelleme
        de `git pull`; onu kullanıcı (ya da bir ajan) çalıştırır.
        """
        def work() -> None:
            try:
                res = U.check(self.cfg, force=True)
            except Exception as exc:  # noqa: BLE001
                rumps.alert(title="Kontrol edilemedi", message=str(exc)[:200], ok="Tamam")
                return
            if res is None:
                rumps.alert(
                    title=f"Sürüm {U.current_version()}",
                    message=("Yayın bilgisi alınamadı: ağ yok, GitHub'da henüz bir "
                             "yayın yok ya da kontrol config'ten kapatılmış "
                             "(updates.enabled)."),
                    ok="Tamam")
                return
            self._update_info = res
            self._refresh_update_item()
            if not res["newer"]:
                rumps.alert(title=f"Güncel — {U.current_version()}",
                            message=f"GitHub'daki son yayın: {res['version']}",
                            ok="Tamam")
                return
            cmd = U.update_command()
            try:
                subprocess.run(["pbcopy"], input=cmd.encode(), check=True)
                copied = True
            except Exception:  # noqa: BLE001
                copied = False
            notes = (res.get("notes") or "").strip()
            rumps.alert(
                title=f"Yeni sürüm: {res['version']}",
                message=(f"Şu an {U.current_version()} kullanıyorsun.\n\n"
                         + (f"{notes[:400]}\n\n" if notes else "")
                         + ("Güncelleme komutu panoya kopyalandı — Terminal'de "
                            "yapıştır:\n\n" if copied else "Terminal'de çalıştır:\n\n")
                         + f"    {cmd}\n\n"
                         "Bu uygulama kendini güncellemez; kurulumu sen (ya da bir "
                         "ajan) yaparsın."),
                ok="Tamam")
            if res.get("url"):
                subprocess.run(["open", res["url"]], check=False)
        threading.Thread(target=work, daemon=True).start()

    def _refresh_snooze_item(self) -> None:
        """Duraklatma varsa kalan süreyi başlıkta göster, yoksa sade hâline dön."""
        info = R.snooze_info()
        if info:
            self.i_snooze.title = (f"Otomatik Kayıt Duraklatıldı — "
                                   f"{info['left'] / 60:.0f} dk")
            self.i_snooze[RESUME].set_callback(self.resume_auto)
        else:
            self.i_snooze.title = "Otomatik Kaydı Duraklat"
            self.i_snooze[RESUME].set_callback(None)

    def toggle_auto(self, _) -> None:
        """Toplantıda otomatik kayıt: mikrofon kullanıma girince kaydı başlat."""
        on = not bool(self.i_auto.state)
        audiowatch.set_autostart(on)
        self.i_auto.state = 1 if on else 0
        if on:
            self._start_watcher()
            w = self.cfg.get("autostart", {})
            notify("Otomatik kayıt açık",
                   f"{w.get('require_apps') or ['herhangi bir uygulama']} mikrofonu "
                   f"{w.get('start_after_s', 15)} sn açık tutunca kayıt başlar.",
                   subtitle="Toplantıya girdiğinde kendiliğinden kaydeder", level="info")
        else:
            notify("Otomatik kayıt kapalı", "Kaydı menüden elle başlatacaksın.",
                   level="quiet")

    def _start_watcher(self) -> None:
        if self._watcher and self._watcher.is_alive():
            return

        def work() -> None:
            try:
                audiowatch.watch_loop(self.cfg, Path(MEET),
                                      should_run=lambda: bool(self.i_auto.state))
            except Exception as exc:  # noqa: BLE001
                notify("⚠ Otomatik kayıt durdu", str(exc)[:180], level="error")

        self._watcher = threading.Thread(target=work, daemon=True)
        self._watcher.start()

    def miccheck(self, _) -> None:
        def work() -> None:
            self.busy = True
            notify("Mikrofon kontrolü", "10 saniye normal sesinle konuş.", level="info")
            r = subprocess.run([MEET, "miccheck", "--auto"], capture_output=True, text=True)
            self.busy = False
            body = (r.stdout or r.stderr).strip()
            summary = body.split("── Değerlendirme")[-1].strip() if "── Değerlendirme" in body else body[-400:]
            rumps.alert(title="Mikrofon Kontrolü", message=body[-1500:], ok="Tamam")
            notify("Mikrofon kontrolü bitti", summary.splitlines()[-1][:120] if summary else "—",
                   level="info")
        threading.Thread(target=work, daemon=True).start()

    def process(self, _) -> None:
        def work() -> None:
            self.busy = True
            r = subprocess.run([MEET, "transcribe"], capture_output=True, text=True)
            self.busy = False
            if r.returncode != 0:
                tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-4:])
                notify("⚠ İşleme başarısız", tail[:200], level="error")
                return
            latest = self._latest()
            tpath = find_output(latest, "transcript.md") if latest else None
            if tpath:
                subprocess.run(["open", str(tpath)], check=False)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # Action item raporu
    # ------------------------------------------------------------------
    def _pending_meetings(self) -> list[Path]:
        """Rapor bekleyen toplantılar, eskiden yeniye.

        `.skip` işaretli klasörler sayılmaz — kullanıcı bazı toplantıların raporunu
        istemiyor ve onların listeyi sonsuza kadar şişirmesi anlamsız.
        """
        root = meetings_dir(self.cfg)
        if not root.exists():
            return []
        return sorted(
            (d for d in root.iterdir()
             if d.is_dir() and has_output(d, "transcript.md")
             and not has_output(d, "action-items.md")
             and not (d / ".skip").exists()),
            key=lambda p: p.name)

    @staticmethod
    def _label(d: Path) -> str:
        """Menüde gösterilecek kısa etiket: tarih-saat + süre."""
        try:
            import json
            meta = json.loads(find_output(d, "transcript.json").read_text())["meta"]
            mins = int(float(meta.get("duration", 0)) // 60)
            return f"{d.name.replace('-auto', '')}  ({mins} dk)"
        except Exception:  # noqa: BLE001
            return d.name.replace("-auto", "")

    def _copy_and_open(self, cmd: str, subtitle: str) -> None:
        """Komutu panoya koyar, Claude'u öne getirir ve NE OLDUĞUNU açıkça söyler.

        Bu menü rapor ÜRETMEZ — üretemez. Action item adımı Cowork skill'i üzerinden
        kullanıcının Claude aboneliğiyle çalışıyor (API key yok, ek maliyet yok), yani
        bir Claude oturumu gerekiyor. Menünün yapabileceği tek şey komutu hazırlamak.

        Eskiden bunu yalnızca bir bildirimle söylüyordu. Kullanıcı "hiçbir işlem
        yapıyormuş gibi görünmüyor" dedi ve haklıydı: Claude zaten açıkken `open`
        görünür bir şey yapmıyor, bildirim de kaçırılabiliyor. Artık modal uyarı
        çıkıyor — kaçırılması mümkün değil.
        """
        try:
            subprocess.run(["pbcopy"], input=cmd.encode(), check=True)
            copied = True
        except Exception:  # noqa: BLE001
            copied = False
        subprocess.run(["open", "-b", "com.anthropic.claudefordesktop"], check=False)

        if copied:
            rumps.alert(
                title="Komut panoya kopyalandı",
                message=(f"{subtitle}\n\n"
                         f"Claude açıldı. Sohbete geç ve ⌘V ile yapıştırıp Enter'a bas:\n\n"
                         f"    {cmd}\n\n"
                         "Raporu Claude üretiyor — bu menü yalnızca komutu hazırlar."),
                ok="Tamam")
        else:
            rumps.alert(
                title="Panoya kopyalanamadı",
                message=(f"{subtitle}\n\n"
                         f"Claude'da şu komutu elle yaz:\n\n    {cmd}"),
                ok="Tamam")
        notify("Komut panoya kopyalandı" if copied else "Kopyalanamadı",
               cmd, subtitle=subtitle, level="info")

    def action_last(self, _) -> None:
        pending = self._pending_meetings()
        if not pending:
            return
        d = pending[-1]
        self._copy_and_open(f"/meeting-actions {d.name}", f"Son toplantı: {self._label(d)}")

    def action_all(self, _) -> None:
        pending = self._pending_meetings()
        if not pending:
            return
        self._copy_and_open("/meeting-actions",
                            f"{len(pending)} toplantı — skill turda en fazla 5 işler")

    def action_one(self, sender) -> None:
        d = self._menu_index.get(sender.title)
        if d:
            self._copy_and_open(f"/meeting-actions {d.name}", self._label(d))

    def skip_all(self, _) -> None:
        pending = self._pending_meetings()
        if not pending:
            return
        w = rumps.alert(
            title="Bekleyenleri Atla",
            message=(f"{len(pending)} toplantı rapor beklemekten çıkarılacak.\n\n"
                     "Ses kayıtları ve transkriptler SİLİNMEZ — sadece bu listede "
                     "görünmezler. Sonradan raporunu istersen klasördeki .skip "
                     "dosyasını silmen yeterli."),
            ok="Atla", cancel="Vazgeç")
        if not w:
            return
        for d in pending:
            try:
                (d / ".skip").touch()
            except OSError:
                pass
        notify("Liste temizlendi", f"{len(pending)} toplantı atlandı olarak işaretlendi.",
               level="quiet")

    def open_folder(self, _) -> None:
        subprocess.run(["open", str(meetings_dir(self.cfg))], check=False)

    # ------------------------------------------------------------------
    def _run(self, args: list[str], ok_msg: str) -> None:
        def work() -> None:
            self.busy = True
            r = subprocess.run([MEET, *args], capture_output=True, text=True)
            self.busy = False
            if r.returncode != 0:
                tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-4:])
                notify("⚠ Hata", tail[:220], level="error")
                return
            if args[0] == "start":
                st = read_state()
                sub = f"{st['mic']['device']} · {audiodev.current_output()}" if st else ""
                notify(ok_msg, "Menü çubuğundan durdurabilirsin.", subtitle=sub, level="info")
                if self.lang_choice != "auto":
                    subprocess.run([MEET, "lang", self.lang_choice], capture_output=True)
        threading.Thread(target=work, daemon=True).start()

    def _latest(self) -> Path | None:
        root = meetings_dir(self.cfg)
        dirs = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
        return max(dirs, key=lambda p: p.stat().st_mtime) if dirs else None


if __name__ == "__main__":
    _lock = acquire_single_instance()
    if _lock is None:
        print("Menü çubuğu zaten çalışıyor, ikinci kopya başlatılmadı.")
        raise SystemExit(0)
    hide_from_dock()
    MeetingApp().run()
