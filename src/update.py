"""Yeni sürüm KONTROLÜ — indirme ya da kurulum YOK.

Tasarım kararı: bu araç kullanıcının makinesinde kendi kendini güncellemez.
Kurulum `git clone` + venv olduğu için güncelleme de `git pull`'dur; kendi
kendini güncelleyen bir kayıt yazılımı, toplantının ortasında kodunu değiştirme
riski taşır. Burada yapılan tek şey GitHub'daki en son yayına bakıp "yeni sürüm
var" demek ve komutu panoya vermek.

GİZLİLİK: bu, projenin yaptığı tek kendiliğinden ağ çağrısıdır (modelleri indirme
dışında). Gönderilen şey GitHub'ın herkese açık API'sine düz bir GET isteğidir;
ne transkript, ne toplantı bilgisi, ne kullanıcı kimliği. GitHub'ın gördüğü, her
web isteğinde gördüğü şeydir: IP adresi ve ajan satırı. `updates.enabled: false`
ile tamamen kapanır ve o zaman hiçbir istek yapılmaz.

Kontrol günde bir kezden sık yapılmaz (`.update-check.json` önbelleği) ve ağ
yoksa sessizce vazgeçer — kayıt akışını hiçbir koşulda etkilemez.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
CACHE = ROOT / ".update-check.json"
TIMEOUT_S = 5
UA = "meeting-pipeline-update-check"


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple:
    """'v1.2.3' / '1.2.3-beta.1' → karşılaştırılabilir demet.

    Ön sürüm (`-beta`) etiketi olan bir sürüm, aynı sayıların etiketsiz hâlinden
    ÖNCE gelir: 1.2.0-rc1 < 1.2.0. Etiket yoksa sonsuz sayılır.
    """
    v = (v or "").strip().lstrip("vV")
    core, _, pre = v.partition("-")
    nums = [int(x) for x in re.findall(r"\d+", core)[:3]] or [0]
    while len(nums) < 3:
        nums.append(0)
    # pre varsa (0, etiket), yoksa (1, "") — demet karşılaştırması doğru sıralar
    return (*nums, 0, pre) if pre else (*nums, 1, "")


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _fetch_latest(repo: str) -> dict | None:
    """GitHub'ın en son YAYINI. Yayın yoksa en son etikete düşer."""
    for url, pick in (
        (f"https://api.github.com/repos/{repo}/releases/latest",
         lambda d: {"version": d.get("tag_name", ""),
                    "url": d.get("html_url", ""),
                    "notes": (d.get("body") or "").strip()}),
        (f"https://api.github.com/repos/{repo}/tags",
         lambda d: ({"version": d[0].get("name", ""),
                     "url": f"https://github.com/{repo}/releases",
                     "notes": ""} if d else None)),
    ):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                data = json.loads(r.read().decode("utf-8"))
            got = pick(data)
            if got and got.get("version"):
                return got
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
            continue          # ağ yok, oran sınırı, yayın yok — hepsi sessiz
    return None


def read_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check(cfg: dict, force: bool = False) -> dict | None:
    """{'version', 'url', 'notes', 'newer'} ya da None.

    `force` değilse `check_interval_h` dolmadan ağa çıkmaz; önbellekteki sonucu
    döner. Kapalıysa hiçbir istek yapılmaz.
    """
    u = cfg.get("updates") or {}
    if not u.get("enabled", True):
        return None
    repo = u.get("repo")
    if not repo:
        return None

    now = time.time()
    cached = read_cache()
    interval = float(u.get("check_interval_h", 24)) * 3600
    if not force and cached.get("checked_at") and now - cached["checked_at"] < interval:
        latest = cached.get("latest")
        if not latest:
            return None
        return {**latest, "newer": is_newer(latest["version"], current_version())}

    # Güncelleme kontrolü HİÇBİR koşulda çağıranı patlatmamalı: bu kod menü
    # çubuğundan ve kayıt akışının yanından çağrılıyor, bir ağ hatası yüzünden
    # kaydın etkilenmesi kabul edilemez.
    try:
        latest = _fetch_latest(repo)
    except Exception:  # noqa: BLE001
        return None
    try:
        CACHE.write_text(json.dumps({"checked_at": now, "latest": latest}),
                         encoding="utf-8")
    except OSError:
        pass
    if not latest:
        return None
    return {**latest, "newer": is_newer(latest["version"], current_version())}


def update_command() -> str:
    """Kullanıcının çalıştıracağı komut. Kurulumu biz YAPMIYORUZ."""
    return (f"cd {ROOT} && git pull && "
            f"./venv/bin/pip install -q -r requirements.txt && ./bin/meet check")


def cmd_version(cfg: dict, force: bool = True) -> None:
    cur = current_version()
    print(f"meeting-pipeline {cur}")
    u = cfg.get("updates") or {}
    if not u.get("enabled", True):
        print("  güncelleme kontrolü kapalı (config: updates.enabled)")
        return
    print(f"  {u.get('repo')} kontrol ediliyor…")
    res = check(cfg, force=force)
    if res is None:
        print("  kontrol edilemedi (ağ yok ya da henüz yayın yok)")
        return
    if res["newer"]:
        print(f"\n  ▸ YENİ SÜRÜM: {res['version']}")
        print(f"    {res['url']}")
        if res.get("notes"):
            for line in res["notes"].splitlines()[:8]:
                print(f"    | {line}")
        print(f"\n  Güncellemek için:\n    {update_command()}")
    else:
        print(f"  güncel ({res['version']} yayında)")
