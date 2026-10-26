"""Menü çubuğu uygulamasını açılışta otomatik başlatan launchd agent'ı kurar/kaldırır.

  meet install-agent     kur ve hemen başlat
  meet uninstall-agent   kaldır
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.meeting-pipeline.menubar"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / "Library" / "Logs" / "meeting-pipeline-menubar.log"

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>{src}</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>TOKENIZERS_PARALLELISM</key><string>false</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
"""


def _uid() -> str:
    return subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()


def install() -> None:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_text(TEMPLATE.format(
        label=LABEL,
        python=ROOT / "venv" / "bin" / "python",
        script=ROOT / "src" / "menubar.py",
        src=ROOT / "src",
        log=LOG,
    ))
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(PLIST)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"HATA: launchctl bootstrap başarısız:\n  {r.stderr.strip()}")
        raise SystemExit(1)
    print(f"✓ Menü çubuğu uygulaması kuruldu ve başlatıldı.\n"
          f"  Her açılışta kendiliğinden gelecek.\n"
          f"  plist: {PLIST}\n"
          f"  log  : {LOG}\n\n"
          f"  İlk kayıtta macOS mikrofon izni isteyecek — İZİN VER'e bas.\n"
          f"  Bildirimler için: Sistem Ayarları > Bildirimler > terminal-notifier açık olmalı.")


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    subprocess.run(["pkill", "-f", "src/menubar.py"], capture_output=True)
    print("✓ Menü çubuğu agent'ı kaldırıldı.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    else:
        raise SystemExit("kullanım: agent.py {install|uninstall}")
