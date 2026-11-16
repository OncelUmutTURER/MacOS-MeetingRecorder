"""Menü alt menüsü gerçekten kuruluyor mu — rumps'ın tuzağıyla birlikte.

Kullanıcı 2026-11-16'da bildirdi: *"11-11 14:48 toplantısının transcript'i
oluşmamış ama uygulamadaki 'transcript çıkar' menüsü disabled, yani bu meeting'i
görmüyor."* Bekleyen toplantıyı bulan kural doğruydu (bkz. test_pending_
transcripts.py); kurulmayan şey MENÜNÜN KENDİSİYDİ.

rumps'ta hiç çocuğu olmayan bir `MenuItem`'ın alt menü nesnesi henüz yoktur ve
`clear()` AttributeError atar. O istisnayı yakalayıp ÇIKMAK kalıcı bir kilit
yaratıyor: madde hiç çocuk almaz → alt menü hiç yaratılmaz → bir sonraki turda
aynı istisna. macOS çocuğu ve callback'i olmayan maddeyi gri gösterdiği için
özellik 17 gün boyunca hiç çalışmadı ve dışarıdan "devre dışı bırakılmış" gibi
göründü.

Burada rumps sahte bir modülle değiştirilip GERÇEK `_refresh_process_item`
çalıştırılıyor; sahte `MenuItem` tam olarak rumps gibi davranıyor: ilk `add()`
gelene kadar `clear()` AttributeError atıyor.
"""
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class FakeMenuItem:
    """rumps.MenuItem'ın davranışı: alt menü ilk `add()` ile doğar."""

    def __init__(self, title, callback=None):
        self.title = title
        self.callback = callback
        self._menu = None          # rumps'taki gibi: başta YOK
        self.children: list = []

    def add(self, item):
        self._menu = self._menu or "NSMenu"
        self.children.append(item)

    def clear(self):
        if self._menu is None:
            # rumps'ın attığı istisnanın aynısı.
            raise AttributeError(
                "'NoneType' object has no attribute 'removeAllItems'")
        self.children = []

    def set_callback(self, cb):
        self.callback = cb


def _install_fake_rumps() -> None:
    fake = types.ModuleType("rumps")
    fake.MenuItem = FakeMenuItem
    fake.separator = object()
    fake.App = type("App", (), {"__init__": lambda self, *a, **k: None})
    fake.timer = lambda _s: (lambda f: f)
    fake.alert = lambda *a, **k: None
    fake.notification = lambda *a, **k: None
    fake.quit_application = lambda *a, **k: None
    sys.modules["rumps"] = fake


class Stub:
    """`_refresh_process_item`'ın dokunduğu asgari nesne."""
    MAX_LISTED = 12

    def __init__(self, root: Path):
        self.root = root
        self.i_process = FakeMenuItem("Transkript Çıkar")

    def untranscribed(self):
        return sorted((d for d in self.root.iterdir() if d.is_dir()),
                      key=lambda p: p.name, reverse=True)

    def transcribe_one(self, sender):
        pass

    # Etiketleme gerçek koddan gelsin; testin ilgilendiği şey menünün KURULMASI.
    _audio_label = staticmethod(lambda d: d.name.replace("-auto", ""))


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'✓' if ok else '✗'}  {label}"
          + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def main() -> int:
    _install_fake_rumps()
    import menubar  # noqa: E402  (sahte rumps kurulduktan SONRA)

    refresh = menubar.MeetingApp._refresh_process_item
    ok = True
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "2026-11-11-1448-auto").mkdir()
        (tmp / "2026-11-06-1830-auto").mkdir()
        app = Stub(tmp)

        # İlk tur: clear() AttributeError atacak. Eski kod burada çıkıyordu.
        refresh(app)
        ok &= check("ilk turda alt menü kuruldu", len(app.i_process.children), 2)
        ok &= check("madde tıklanabilir çocuk taşıyor",
                    all(c.callback is not None for c in app.i_process.children), True)
        ok &= check("başlık sayıyı gösteriyor",
                    app.i_process.title, "Transkript Çıkar (2 bekliyor)")
        first = app.i_process.children[0].title if app.i_process.children else ""
        ok &= check("en yeni önce", first.startswith("2026-11-11"), True)

        # İkinci tur: liste aynı, menü yeniden kurulmamalı (açık menü kapanmasın).
        before = list(app.i_process.children)
        refresh(app)
        ok &= check("liste değişmeyince menü dokunulmuyor",
                    app.i_process.children, before)

        # Liste değişince yeniden kurulmalı — ve clear() artık çalışıyor.
        (tmp / "2026-11-14-0900-auto").mkdir()
        refresh(app)
        ok &= check("liste değişince yeniden kuruluyor",
                    len(app.i_process.children), 3)

        # Hepsi bitince: boş durumda da bir çocuk olmalı, yoksa madde yine gri kalır.
        for d in list(tmp.iterdir()):
            shutil.rmtree(d)
        refresh(app)
        ok &= check("bekleyen yokken de çocuk var (gri kalmasın)",
                    len(app.i_process.children), 1)
        ok &= check("boş durumda başlık", app.i_process.title,
                    "Transkripti Bekleyen Yok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
