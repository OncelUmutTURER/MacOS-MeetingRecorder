"""macOS bildirimleri.

terminal-notifier varsa onu kullanır: kendi bundle kimliği olduğu için Sistem
Ayarları > Bildirimler altında görünür, izin verilebilir ve bildirime tıklayınca
bir klasör/dosya açılabilir. Yoksa osascript'e düşer (o da çalışır ama izinleri
Script Editor'a bağlıdır ve sessizce bastırılabilir).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_TN = shutil.which("terminal-notifier") or "/opt/homebrew/bin/terminal-notifier"

LEVELS = {
    "info": "Glass",
    "warn": "Basso",
    "error": "Sosumi",
    "quiet": None,
}


def notify(title: str, message: str, *, subtitle: str = "", level: str = "info",
           open_path: Path | str | None = None, open_url: str | None = None,
           group: str = "meeting-pipeline") -> None:
    """Bildirim gönderir. Hiçbir koşulda çağıranı patlatmaz."""
    try:
        if Path(_TN).exists():
            cmd = [_TN, "-title", title, "-message", message, "-group", group]
            if subtitle:
                cmd += ["-subtitle", subtitle]
            sound = LEVELS.get(level)
            if sound:
                cmd += ["-sound", sound]
            # open_url yalnızca http(s) kabul eder: bildirime tıklayınca rastgele
            # bir şema açmak istemiyoruz.
            if open_url and open_url.startswith(("https://", "http://")):
                cmd += ["-open", open_url]
            elif open_path:
                cmd += ["-open", f"file://{Path(open_path).resolve()}"]
            subprocess.run(cmd, capture_output=True, timeout=5)
            return
        body = f"{subtitle}\n{message}" if subtitle else message
        subprocess.run(
            ["osascript", "-e",
             f"display notification {json.dumps(body)} with title {json.dumps(title)}"],
            capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001 — bildirim hiçbir zaman akışı durdurmamalı
        pass
