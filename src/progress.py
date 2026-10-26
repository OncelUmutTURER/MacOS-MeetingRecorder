"""İşleme ilerlemesi — arka plandaki pipeline yazar, menü çubuğu okur.

97 dakikalık bir toplantının transkripti ~20 dakika sürüyor. Bu sürede kullanıcının
tek gördüğü dönen bir ikon olursa takılıp takılmadığını anlayamıyor. Pipeline her
adımda toplantı klasörüne progress.json yazıyor, menü çubuğu onu okuyup yüzde ve
kalan süre gösteriyor.

Ağırlıklar ölçülmüş sürelere göre: 97 dakikalık kayıtta sistem kanalı mikrofon
kanalından belirgin uzun sürdü (daha çok konuşma penceresi), diarization tek geçiş.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILE = "progress.json"

# (aşama adı, göreli ağırlık, ekranda görünecek etiket)
STAGES: list[tuple[str, float, str]] = [
    # SIRA ÖNEMLİ: mikrofon önce işleniyor (dil önseli oradan çıkıyor), ilerleme
    # çubuğunun geri sıçramaması için aşama sırası da aynı olmalı.
    ("vad-mic", 0.02, "mikrofon kanalı taranıyor"),
    ("whisper-mic", 0.28, "senin sesin yazıya dökülüyor"),
    ("vad-sys", 0.03, "sistem kanalı taranıyor"),
    ("whisper-sys", 0.42, "karşı taraf yazıya dökülüyor"),
    ("diarize", 0.20, "konuşmacılar ayrıştırılıyor"),
    ("merge", 0.05, "birleştiriliyor"),
]


class Progress:
    """Aşama aşama ilerleme yazar. Hiçbir hata çağıranı durdurmaz."""

    def __init__(self, session: Path, enabled: bool = True) -> None:
        self.session = Path(session)
        self.enabled = enabled
        self.started = time.time()
        self.stage_name = ""
        self.stage_label = ""
        self.base = 0.0          # bu aşamadan önce tamamlanmış oran
        self.weight = 0.0
        self.frac = 0.0          # aşama içi 0..1
        self.detail = ""
        self._write()

    # -- kullanım -----------------------------------------------------------
    def stage(self, name: str, detail: str = "") -> None:
        done = 0.0
        weight, label = 0.0, name
        for n, w, l in STAGES:
            if n == name:
                weight, label = w, l
                break
            done += w
        self.base, self.weight = done, weight
        self.stage_name, self.stage_label = name, label
        self.frac, self.detail = 0.0, detail
        self._write()

    def tick(self, done: int, total: int, detail: str = "") -> None:
        self.frac = min(1.0, done / total) if total else 0.0
        if detail:
            self.detail = detail
        self._write()

    def finish(self) -> None:
        if not self.enabled:
            return
        try:
            (self.session / FILE).unlink(missing_ok=True)
        except OSError:
            pass

    # -- iç ------------------------------------------------------------------
    @property
    def pct(self) -> float:
        return min(99.0, 100.0 * (self.base + self.weight * self.frac))

    def _write(self) -> None:
        if not self.enabled:
            return
        pct = self.pct
        elapsed = time.time() - self.started
        eta = (elapsed / pct * (100 - pct)) if pct > 3 else None
        payload = {
            "pct": round(pct, 1),
            "stage": self.stage_name,
            "label": self.stage_label,
            "detail": self.detail,
            "elapsed_s": round(elapsed),
            "eta_s": round(eta) if eta else None,
            "updated": time.time(),
            "pid": os.getpid(),
            "session": self.session.name,
        }
        try:
            tmp = self.session / (FILE + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.session / FILE)     # atomik
        except OSError:
            pass


def read(session: Path) -> dict | None:
    """Bir toplantının ilerlemesi. Süreç ölmüşse None döner."""
    f = Path(session) / FILE
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # Süreç öldüyse bayat dosya kalmasın
    pid = data.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
        except (OSError, ProcessLookupError, ValueError):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
            return None
    if time.time() - data.get("updated", 0) > 300:
        return None
    return data


def latest(meetings_dir: Path) -> dict | None:
    """Şu an işlenen toplantının ilerlemesi (varsa)."""
    root = Path(meetings_dir)
    if not root.exists():
        return None
    for d in sorted((p for p in root.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        if (data := read(d)) is not None:
            return data
    return None


def human_eta(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return ""
    s = int(seconds)
    if s < 60:
        return f"~{s} sn"
    if s < 3600:
        return f"~{s // 60} dk"
    return f"~{s // 3600} sa {(s % 3600) // 60} dk"
