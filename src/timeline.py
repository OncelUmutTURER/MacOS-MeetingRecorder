"""Dosya zamanı ↔ duvar saati eşlemesi.

SORUN (ölçüldü 2026-10-21, 119 kayıt): avfoundation bildirdiği hızda örnek
üretmiyor. Yakalanan dosya, ffmpeg'in loguna yazdığı gerçek geçen süreden
**medyan %6.4 kısa** çıkıyor; mikrofonda %8-10, bir kayıtta %13.8. Kayıp sabit
de değil: aynı toplantının dilimlerinde %5 ile %18 arasında geziniyor.

Sonuç: `sys.wav` içindeki 40. dakika gerçekte 43. dakikadır. İki kanal farklı
oranlarda kaydığı için birleşik transkriptte senin cümlelerinle karşı tarafın
cümleleri yanlış sıraya giriyor — kullanıcı bunu "senkron sorunu transcript'in
anlaşılmasını zorlaştırmaz mı" diye sordu ve haklıydı.

NEDEN SESİ DÜZELTMİYORUZ: `aresample=async` boşlukları sessizlikle doldurup
dosyayı duvar saatine oturtuyor (ölçüldü: kayma dakikada 2.82 sn → 0.05 sn).
Ama enjekte edilen 103 ms'lik sessizlikler konuşmanın ortasına düşüyor ve
transkripti ÖLÇÜLEBİLİR şekilde bozuyor: aynı 10 dakikalık gerçek kayıtta
1536 → 1506 kelime (%2 kayıp), metin benzerliği %96.1, bir jargon terimi kayboldu.
Kontrol ölçümü: whisper aynı dosyada iki çalıştırmada **%100 aynı** sonucu
veriyor, yani o %2 gürültü değil, gerçek kayıp. Kullanıcının önceliği net:
"transcript'in doğruluğu highest priority".

ÇÖZÜM: ses ham kalır (whisper en iyi metni üretir), zaman damgaları sonradan
düzeltilir. Veri zaten elimizde: ffmpeg ilerleme satırları hem yazılan bayt
sayısını (`size=`) hem gerçek geçen süreyi (`time=`) içeriyor, saniyede iki kez.
Bu, dosya zamanı ile duvar saati arasında parça parça bir harita demektir.

    size=  7168KiB time=00:04:12.07   ->  dosya 229.3 sn = duvar 252.1 sn

Harita `timeline.json` olarak kaydediliyor (loglar sonradan temizlense de kalsın).
`meet split` / `meet trim` sese dokunurken `to_file()` ile ters çeviriyor:
kullanıcı transkriptten okuduğu saati veriyor, ses o saatin DOSYA karşılığından
kesiliyor.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PAIR_RE = re.compile(r"size=\s*(\d+)\s*KiB time=(\d+):(\d+):([\d.]+)")
EPOCH_RE = re.compile(r"start:\s*([0-9]+\.[0-9]+)")
WAV_HEADER = 44
NAME = "timeline.json"


def _read(p: Path) -> str:
    try:
        return p.read_bytes().decode("utf-8", "replace").replace("\r", "\n")
    except OSError:
        return ""


def log_pairs(log: Path, sr: int) -> list[tuple[float, float]]:
    """ffmpeg ilerleme satırlarından (dosya_s, duvar_s) çiftleri.

    `size=` çıkış dosyasının o anki boyutu (KiB), `time=` ise
    `-use_wallclock_as_timestamps` sayesinde gerçek geçen süre.
    """
    out: list[tuple[float, float]] = []
    for m in PAIR_RE.finditer(_read(log)):
        kib = int(m.group(1))
        file_s = max(0.0, (kib * 1024 - WAV_HEADER) / (sr * 2))
        wall = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + float(m.group(4))
        if out and file_s < out[-1][0] - 0.05:
            continue                      # geriye giden örnek: atla
        out.append((file_s, wall))
    return out


def _epoch(log: Path) -> float | None:
    m = EPOCH_RE.search(_read(log))
    return float(m.group(1)) if m else None


def _thin(pairs: list[tuple[float, float]], every: float = 5.0
          ) -> list[tuple[float, float]]:
    """Haritayı seyreltir: saniyede iki örnek gereksiz, 5 saniyede bir yeter."""
    if not pairs:
        return []
    out = [pairs[0]]
    for f, w in pairs[1:]:
        if f - out[-1][0] >= every:
            out.append((f, w))
    if out[-1] != pairs[-1]:
        out.append(pairs[-1])
    return out


def mic_parts(session: Path) -> tuple[list[tuple[float, Path]], bool]:
    """(parçanın duvar saatindeki başlangıcı, log yolu) listesi · liste tam mı.

    `_assemble_mic` parçaları tam bu gecikmelerle diziyor; harita da aynı
    gecikmeleri kullanmazsa mic.wav içindeki konumlar tutmaz.

    Birincil kaynak `mic.parts.json` (birleştirme anında yazılır). Parça
    loglarından türetmek YETMİYOR: `meet audit --fix` logları siliyor ve geriye
    tek log kalınca harita sessizce yanlış çıkıyor — ölçüldü, 17 parçalı bir
    kayıtta mikrofon haritası 63 dakika kaymış görünüyordu. Bu yüzden ikinci
    dönüş değeri "bu liste güvenilir mi" diyor.
    """
    pmap = session / "mic.parts.json"
    if pmap.exists():
        try:
            d = json.loads(pmap.read_text(encoding="utf-8"))
            out = [(float(p["delay_s"]), session / p["log"]) for p in d["parts"]]
            return out, all((session / p["log"]).exists() for p in d["parts"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass
    ref = _epoch(session / "sys.ffmpeg.log")
    logs = sorted(session.glob("mic.part*.log"))
    out = []
    for log in logs:
        ep = _epoch(log)
        delay = max(0.0, ep - ref) if (ep and ref) else 0.0
        out.append((delay, log))
    # Parça numaraları 1'den başlayıp kesintisiz gitmiyorsa elimizde eksik var.
    nums = sorted(int(re.search(r"part(\d+)", p.name).group(1)) for p in logs
                  if re.search(r"part(\d+)", p.name))
    complete = bool(nums) and nums == list(range(1, len(nums) + 1))
    return out, complete


def _wav_seconds(p: Path, sr: int) -> float:
    try:
        return max(0.0, (p.stat().st_size - WAV_HEADER) / (sr * 2))
    except OSError:
        return 0.0


def _sane(pairs: list[tuple[float, float]], wav: Path, sr: int, label: str
          ) -> bool:
    """Harita gerçekten bu dosyayı anlatıyor mu.

    YANLIŞ HARİTA, HARİTASIZLIKTAN KÖTÜDÜR: saatleri sessizce bozar. İki kaba
    ama yeterli kontrol — başı sıfıra yakın olmalı ve dosyanın çoğunu kapsamalı.
    """
    if len(pairs) < 2:
        return False
    if pairs[0][0] > 2.0 or pairs[0][1] > 5.0:
        print(f"  ⚠ {label} zaman haritası dosyanın başını tutmuyor "
              f"(dosya {pairs[0][0]:.0f} sn / duvar {pairs[0][1]:.0f} sn) — "
              f"kullanılmıyor")
        return False
    have = _wav_seconds(wav, sr)
    if have and pairs[-1][0] < have * 0.8:
        print(f"  ⚠ {label} zaman haritası kaydın yalnızca "
              f"%{pairs[-1][0] / have * 100:.0f}'ini kapsıyor — kullanılmıyor "
              f"(parça logları silinmiş olabilir)")
        return False
    return True


def build(session: Path, cfg: dict) -> dict[str, list[list[float]]]:
    """İki kanal için de dosya→duvar haritası üretir."""
    sr = int(cfg["audio"]["work_sample_rate"])
    maps: dict[str, list[list[float]]] = {}

    sysp = log_pairs(session / "sys.ffmpeg.log", sr)
    if sysp and _sane(sysp, session / "sys.wav", sr, "sys"):
        maps["sys"] = [[f, w] for f, w in _thin(sysp)]

    parts, complete = mic_parts(session)
    if parts and not complete:
        print("  ⚠ mikrofon parça logları eksik — mic zaman haritası kurulmadı")
        parts = []
    micp: list[tuple[float, float]] = []
    if len(parts) == 1:
        # Tek parça: `_assemble_mic` gecikme UYGULAMADAN dosyayı olduğu gibi
        # mic.wav yapıyor, yani dosya konumu = parçanın kendi konumu.
        micp = [(f, parts[0][0] + w) for f, w in log_pairs(parts[0][1], sr)]
    else:
        for delay, log in parts:
            for f, w in log_pairs(log, sr):
                micp.append((delay + f, delay + w))
        micp.sort(key=lambda x: x[0])
    # Harita monoton olmalı: parçalar çakışırsa (ölmeyen eski ffmpeg) duvar
    # saati geri gidebilir, interpolasyon bozulur.
    mono: list[tuple[float, float]] = []
    for f, w in micp:
        if mono and w < mono[-1][1]:
            continue
        mono.append((f, w))
    if mono and _sane(mono, session / "mic.wav", sr, "mic"):
        maps["mic"] = [[f, w] for f, w in _thin(mono)]
    return maps


def save(session: Path, cfg: dict) -> dict | None:
    maps = build(session, cfg)
    if not maps:
        return None
    drift = {}
    for ch, m in maps.items():
        if len(m) > 1 and m[-1][0] > 0:
            drift[ch] = round(m[-1][1] - m[-1][0], 2)
    data = {"maps": maps, "drift_s": drift, "sample_rate": cfg["audio"]["work_sample_rate"]}
    (session / NAME).write_text(json.dumps(data), encoding="utf-8")
    return data


def load(session: Path) -> dict | None:
    p = session / NAME
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d if d.get("maps") else None


def _interp(pairs: list[list[float]], t: float, src: int, dst: int) -> float:
    """pairs[i] = [dosya, duvar]; src/dst hangi sütundan hangisine."""
    if not pairs:
        return t
    if t <= pairs[0][src]:
        return t + (pairs[0][dst] - pairs[0][src])
    if t >= pairs[-1][src]:
        return t + (pairs[-1][dst] - pairs[-1][src])
    lo, hi = 0, len(pairs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pairs[mid][src] <= t:
            lo = mid
        else:
            hi = mid
    a, b = pairs[lo], pairs[hi]
    span = b[src] - a[src]
    if span <= 0:
        return a[dst]
    return a[dst] + (b[dst] - a[dst]) * (t - a[src]) / span


def to_wall(pairs: list[list[float]], t: float) -> float:
    return _interp(pairs, t, 0, 1)


def to_file(pairs: list[list[float]], t: float) -> float:
    return _interp(pairs, t, 1, 0)


def apply(pairs: list[list[float]], segs: list[dict]) -> list[dict]:
    """Segmentlerin start/end değerlerini duvar saatine taşır."""
    out = []
    for s in segs:
        s = dict(s)
        s["start"] = round(to_wall(pairs, float(s["start"])), 3)
        s["end"] = round(to_wall(pairs, float(s["end"])), 3)
        out.append(s)
    return out


def audio_time(session: Path, t: float, channel: str = "sys") -> float:
    """Transkriptten okunan bir saati, sesin İÇİNDEKİ konuma çevirir.

    `meet split` ve `meet trim` sese dokunurken bunu kullanır: kullanıcı
    transkriptten okuduğu saati veriyor, ses o saatin dosya karşılığından
    kesilmeli. Harita yoksa aynen döner (eski kayıtlar bozulmaz).
    """
    d = load(session)
    if not d:
        return t
    # Başka kanalın haritasına DÜŞME: kanallar farklı oranlarda kayıyor,
    # yanlış haritayla çevirmek hiç çevirmemekten kötü.
    pairs = d["maps"].get(channel)
    return to_file(pairs, t) if pairs else t


def wall_time(session: Path, t: float, channel: str = "sys") -> float:
    """Ses dosyasındaki bir konumu transkript saatine çevirir (ters yön)."""
    d = load(session)
    if not d:
        return t
    pairs = d["maps"].get(channel)
    return to_wall(pairs, t) if pairs else t
