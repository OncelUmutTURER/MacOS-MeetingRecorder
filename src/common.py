"""Ortak yardımcılar: config yükleme, avfoundation cihaz keşfi, yollar."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# MEET_CONFIG ile geçici olarak başka bir config dosyası kullanılabilir (test/deneme için).
CONFIG_PATH = Path(os.environ.get("MEET_CONFIG") or ROOT / "config.yaml")
STATE_PATH = ROOT / ".state.json"
ENV_PATH = ROOT / ".env"
FFMPEG = os.environ.get("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = ROOT / "config.example.yaml"
        hint = (f"\n  cp {example.name} {CONFIG_PATH.name}\n"
                f"\nThen edit it: microphone priority, output profiles and the\n"
                f"meetings folder. See docs/SETUP.md." if example.exists() else "")
        raise SystemExit(f"ERROR: {CONFIG_PATH} not found.{hint}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def meetings_dir(cfg: dict) -> Path:
    return Path(os.path.expanduser(cfg["paths"]["meetings_dir"]))


# ==========================================================================
# Çıktı dosyası adları
# ==========================================================================
# Çıktılar toplantı damgasını ADINDA taşır: `2026-09-09-1110-transcript.md`.
# Sebep: kullanıcı bu dosyaları meslektaşlarıyla paylaşıyor ve hepsinin adı
# `transcript.md` olunca ekler birbirine karışıyordu. (Bu, iki klasörde görülen
# `transcript (2026-09-09-1110).md` adlandırmasının da açıklaması: onları
# paylaşmak için kullanıcı elle yeniden adlandırmış — otomasyon değildi.)
#
# ESKİ ADLAR HÂLÂ OKUNUR. Okuyan her kod `find_output()` kullanmalı; yalnızca
# yazan kod `output_path()` kullanır.

STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{4})")

LEGACY_NAMES = {
    "transcript.md": "transcript.md",
    "transcript.json": "transcript.json",
    "action-items.md": "action-items.md",
}


def session_stamp(session: Path) -> str:
    """Klasör adından `2026-09-09-1110` damgasını çıkarır.

    Klasör adı `2026-09-09-1110-auto` ya da `2026-08-20-2108-guard-test` gibi;
    damga her zaman baştaki 15 karakter. Beklenen kalıba uymuyorsa klasör adının
    kendisi kullanılır (elle oluşturulmuş klasörler bozulmasın).
    """
    m = STAMP_RE.match(session.name)
    return m.group(1) if m else session.name


def output_path(session: Path, kind: str) -> Path:
    """YAZILACAK dosyanın yolu. kind: 'transcript.md' | 'transcript.json' | 'action-items.md'"""
    if kind not in LEGACY_NAMES:
        raise ValueError(f"bilinmeyen çıktı türü: {kind}")
    stem, _, ext = kind.rpartition(".")
    return session / f"{session_stamp(session)}-{stem}.{ext}"


def find_output(session: Path, kind: str) -> Path | None:
    """OKUNACAK dosyanın yolu: önce yeni ad, sonra eski ad. Yoksa None."""
    new = output_path(session, kind)
    if new.exists():
        return new
    old = session / LEGACY_NAMES[kind]
    return old if old.exists() else None


def has_output(session: Path, kind: str) -> bool:
    return find_output(session, kind) is not None


def looks_like_session(p: Path) -> bool:
    """Bu klasör bir toplantı kaydı mı?

    Ölçüt adla sınırlı değil: elle adlandırılmış klasörler de kabul edilsin diye
    içerikteki tipik dosyalara da bakılıyor.
    """
    if not p.is_dir():
        return False
    if any((p / n).exists() for n in ("sys.wav", "mic.wav")):
        return True
    return has_output(p, "transcript.json") or has_output(p, "transcript.md")


def resolve_session(arg: str | None = None) -> Path:
    """Klasör argümanını çözer; verilmemişse BULUNULAN dizini kullanır.

    Kullanıcı bir toplantı klasörünün içindeyken `meet split --at 01:12:30` yazınca
    ayrıca yol vermek zorunda kalmasın diye. Yanlışlıkla rastgele bir dizinde
    çalıştırmayı önlemek için içerik doğrulanır.
    """
    if arg:
        p = Path(arg).expanduser()
        if not p.is_dir():
            raise SystemExit(f"HATA: klasör yok: {p}")
        return p.resolve()

    p = Path.cwd()
    if looks_like_session(p):
        return p.resolve()
    # bir üst dizin de olabilir (kullanıcı klasörün içinde bir alt klasördeyse)
    if looks_like_session(p.parent):
        return p.parent.resolve()
    raise SystemExit(
        f"HATA: burası bir toplantı klasörü değil:\n  {p}\n\n"
        f"Ya bir toplantı klasörünün içine gir, ya da yolu ver:\n"
        f"  <komut> ~/Meetings/2026-09-28-1307-auto ...")


def load_env() -> dict:
    """.env dosyasını KEY=VALUE olarak okur. Değerler ortama da yazılır."""
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            env[key.strip()] = val
            os.environ.setdefault(key.strip(), val)
    return env


def read_state() -> dict | None:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return None
    return None


def write_state(state: dict | None) -> None:
    if state is None:
        STATE_PATH.unlink(missing_ok=True)
    else:
        STATE_PATH.write_text(json.dumps(state, indent=2))


# --------------------------------------------------------------------------
# avfoundation cihaz keşfi
# --------------------------------------------------------------------------

_DEV_RE = re.compile(r"^\[AVFoundation[^\]]*\]\s*\[(\d+)\]\s*(.+)$")


def list_audio_devices() -> list[tuple[int, str]]:
    """(index, name) listesi döner."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    out = proc.stderr
    devices: list[tuple[int, str]] = []
    in_audio = False
    for line in out.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
            continue
        if "AVFoundation video devices" in line:
            in_audio = False
            continue
        if not in_audio:
            continue
        m = _DEV_RE.match(line.strip())
        if m:
            devices.append((int(m.group(1)), m.group(2).strip()))
    return devices


def find_device(name: str) -> int:
    """Cihaz adından avfoundation index'i bulur (tam eşleşme, yoksa içerir)."""
    devices = list_audio_devices()
    for idx, dname in devices:
        if dname.lower() == name.lower():
            return idx
    for idx, dname in devices:
        if name.lower() in dname.lower():
            return idx
    listing = "\n".join(f"  [{i}] {n}" for i, n in devices)
    raise SystemExit(
        f"HATA: '{name}' adlı ses cihazı bulunamadı.\n"
        f"Mevcut ses giriş cihazları:\n{listing}\n"
        f"config.yaml içindeki audio.capture_device / mic_device / sys_device değerlerini düzelt."
    )


def probe_channels(device_index: int, sample_rate: int) -> int | None:
    """Cihazın kaç kanal sunduğunu kısa bir deneme kaydıyla tespit eder."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-f", "avfoundation", "-i", f":{device_index}",
         "-t", "0.3", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"Stream #0:0.*?Audio:.*?,\s*\d+\s*Hz,\s*([^,]+),", proc.stderr)
    if not m:
        return None
    layout = m.group(1).strip()
    named = {"mono": 1, "stereo": 2, "3.0": 3, "quad": 4, "4.0": 4, "5.0": 5, "5.1": 6}
    if layout in named:
        return named[layout]
    m2 = re.match(r"(\d+)\s*channels", layout)
    if m2:
        return int(m2.group(1))
    return None


def fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def die(msg: str) -> None:
    print(f"HATA: {msg}", file=sys.stderr)
    raise SystemExit(1)
