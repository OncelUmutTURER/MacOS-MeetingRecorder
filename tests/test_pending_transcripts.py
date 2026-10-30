"""Transkripti bekleyen toplantıların bulunması.

Kullanıcı 2026-10-30'da bildirdi: *"Menubar'daki app menüsünden toplantı
transcript'ini çıkar diyince son toplantınınkini çıkarıyor. Geçmişte kalan bir
toplantının transcript'ini çıkarma şansım yok gibi duruyor."* Haklıydı ve daha
kötüsü, o düğme zaten transkripti OLAN en son toplantıyı yeniden işliyordu:
3 GB model yükleyip asıl bekleyen işle yarışıyordu.

Burada menünün listeyi doğru kurduğu sınanıyor. `menubar` rumps/AppKit
gerektirdiği için içe aktarılmıyor; seçim mantığı aynı kurallarla burada
yeniden kuruluyor ve menubar.py'nin o kuralları kullandığı metin düzeyinde
doğrulanıyor.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common import has_output  # noqa: E402


def pending_transcripts(root: Path) -> list[Path]:
    """menubar.MeetingApp.untranscribed ile AYNI kural."""
    out = []
    for d in root.iterdir():
        if not d.is_dir() or (d / ".skip").exists():
            continue
        if has_output(d, "transcript.md"):
            continue
        if any((d / f).exists() and (d / f).stat().st_size > 1024
               for f in ("sys.wav", "mic.wav")):
            out.append(d)
    return sorted(out, key=lambda p: p.name, reverse=True)


def make(root: Path, name: str, *, audio=True, transcript=False, skip=False,
         tiny=False) -> Path:
    d = root / name
    d.mkdir()
    if audio:
        (d / "sys.wav").write_bytes(b"x" * (100 if tiny else 5000))
        (d / "mic.wav").write_bytes(b"x" * (100 if tiny else 5000))
    if transcript:
        (d / f"{name[:15]}-transcript.md").write_text("# t", encoding="utf-8")
    if skip:
        (d / ".skip").write_text("elle atlandı", encoding="utf-8")
    return d


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'✓' if ok else '✗'}  {label}" + ("" if ok else f"   (={got!r}, beklenen {want!r})"))
    return ok


def main() -> int:
    ok = True
    tmp = Path(tempfile.mkdtemp())
    try:
        make(tmp, "2026-10-20-1619-auto")                      # bekliyor
        make(tmp, "2026-10-29-1758-auto")                      # bekliyor (gerçek vaka)
        make(tmp, "2026-10-29-1854-auto", transcript=True)     # bitmiş
        make(tmp, "2026-10-27-1500-auto", transcript=True)     # bitmiş
        make(tmp, "2026-10-01-0900-auto", skip=True)           # bilerek atlanmış
        make(tmp, "2026-10-02-0900-auto", audio=False)         # sesi yok
        make(tmp, "2026-10-03-0900-auto", tiny=True)           # boş sayılacak kadar küçük
        (tmp / "rastgele-klasor").mkdir()                      # toplantı değil

        pend = pending_transcripts(tmp)
        names = [p.name for p in pend]

        ok &= check("yalnızca bekleyenler", names,
                    ["2026-10-29-1758-auto", "2026-10-20-1619-auto"])
        ok &= check("EN YENİ ÖNCE (kullanıcı dünkünü arıyor)",
                    names[0], "2026-10-29-1758-auto")
        ok &= check("transkripti olan listede YOK",
                    "2026-10-29-1854-auto" in names, False)
        ok &= check(".skip olan listede yok", "2026-10-01-0900-auto" in names, False)
        ok &= check("sesi olmayan listede yok", "2026-10-02-0900-auto" in names, False)
        ok &= check("boş ses dosyası listede yok", "2026-10-03-0900-auto" in names, False)
        ok &= check("toplantı olmayan klasör listede yok",
                    "rastgele-klasor" in names, False)

        print("\nher şey bitmişken:")
        for d in pend:
            (d / f"{d.name[:15]}-transcript.md").write_text("# t", encoding="utf-8")
        ok &= check("liste boşalıyor", pending_transcripts(tmp), [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nmenubar gerçekten bu kuralları kullanıyor mu:")
    src = (ROOT / "src" / "menubar.py").read_text(encoding="utf-8")
    for frag, label in (
        ("def untranscribed", "untranscribed() var"),
        ('has_output(d, "transcript.md")', "transkripti olanı eliyor"),
        ('(d / ".skip").exists()', ".skip'i eliyor"),
        ("reverse=True", "en yeniden eskiye sıralıyor"),
        ("def transcribe_one", "tek toplantı işleme var"),
        ("_refresh_process_item", "menü listeyi yeniliyor"),
    ):
        ok &= check(label, frag in src, True)
    ok &= check("eski 'son toplantıyı işle' düğmesi kalmadı",
                "Son Toplantının Transkriptini Çıkar" in src, False)

    print("\nstop teşhisi (iz bırakmayan otomasyon hata ayıklanamaz):")
    rec = (ROOT / "src" / "record.py").read_text(encoding="utf-8")
    for frag, label in (
        ('"stop.log"', "stop.log yazılıyor"),
        ("_stop_body", "gövde ayrı, sarmalanabiliyor"),
        ("TRANSKRİPT BAŞLAMADI", "başlamadıysa uyarıyor"),
        ("traceback.format_exc()", "istisna loga yazılıyor"),
    ):
        ok &= check(label, frag in rec, True)

    print("\nSONUÇ: " + ("hepsi geçti" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
