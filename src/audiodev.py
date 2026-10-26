"""Ses cihazı yönetimi: varsayılan çıkışı okumak/değiştirmek, cihaz varlığını sorgulamak.

Kayıt sırasında sistem çıkışının doğru Multi-Output cihazında kalmasını sağlar.
Bluetooth kulaklık bağlanınca macOS varsayılan çıkışı ona çeviriyor; bu Multi-Output
zincirini kırıp BlackHole'a giden kolu kesiyor ve loopback kaydı sessizliğe düşüyor.
"""
from __future__ import annotations

import shutil
import subprocess

SWITCH = shutil.which("SwitchAudioSource") or "/opt/homebrew/bin/SwitchAudioSource"


def available() -> bool:
    return shutil.which(SWITCH) is not None or SWITCH.startswith("/opt")


def current_output() -> str | None:
    r = subprocess.run([SWITCH, "-c", "-t", "output"], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def list_outputs() -> list[str]:
    r = subprocess.run([SWITCH, "-a", "-t", "output"], capture_output=True, text=True)
    return [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def set_output(name: str) -> bool:
    r = subprocess.run([SWITCH, "-s", name, "-t", "output"], capture_output=True, text=True)
    return r.returncode == 0


def input_exists(name: str) -> bool:
    r = subprocess.run([SWITCH, "-a", "-t", "input"], capture_output=True, text=True)
    return any(name.lower() == l.strip().lower() for l in r.stdout.splitlines())


def managed_outputs(cfg: dict) -> set[str]:
    """config'te tanımlı Multi-Output adları (küçük harf)."""
    return {(p.get("output") or "").lower()
            for p in (cfg["audio"].get("output_profiles") or []) if p.get("output")}


def sane_idle_output(cfg: dict, candidate: str | None) -> tuple[str | None, str]:
    """Toplantı dışında kalınacak çıkış cihazını seçer.

    NEDEN VAR: `cmd_start` kaydın öncesindeki cihazı `prev_output` diye saklıyor,
    `cmd_stop` da ona geri dönüyordu. Ama bir kez Multi-Output'ta takılı kalındığında
    (temiz durmayan kayıt, çökme, ya da guard'ın çıkışı zorlaması) bir sonraki kayıt
    `prev_output = "Meeting Output SPK"` diye kaydediyor ve bitince oraya "geri
    dönüyor". Cırcır dişlisi gibi: bir kere takıldı mı bir daha kendiliğinden
    çıkamıyor. Kullanıcının "toplantılar bitince ses cihazım hep multi-output'ta
    oluyor" gözleminin sebebi tam olarak bu.

    Çözüm: aday bir Multi-Output ise ya da artık mevcut değilse, `idle_output_priority`
    listesindeki ilk BAĞLI cihaza dön.
    """
    outs = list_outputs()
    lower = {o.lower(): o for o in outs}
    managed = managed_outputs(cfg)

    if candidate and candidate.lower() in lower and candidate.lower() not in managed:
        return lower[candidate.lower()], "önceki cihaz"

    why = "önceki cihaz Multi-Output'tu" if (candidate and candidate.lower() in managed) \
        else (f"'{candidate}' artık bağlı değil" if candidate else "önceki cihaz bilinmiyor")

    for name in cfg["audio"].get("idle_output_priority") or []:
        if name.lower() in lower and name.lower() not in managed:
            return lower[name.lower()], why
    for o in outs:                      # son çare: Multi-Output olmayan ilk cihaz
        if o.lower() not in managed:
            return o, why + ", listedekilerin hiçbiri bağlı değil"
    return None, why + ", uygun cihaz bulunamadı"


def pick_output_profile(cfg: dict) -> tuple[str | None, str]:
    """Bağlı cihazlara göre hangi Multi-Output kullanılmalı.

    Dönen: (cihaz adı ya da None, gerekçe)
    """
    profiles = cfg["audio"].get("output_profiles") or []
    outs = {o.lower() for o in list_outputs()}
    for p in profiles:
        need = p.get("match_input")
        if need and not input_exists(need):
            continue
        target = p.get("output")
        if target and target.lower() in outs:
            return target, (f"{need} bağlı" if need else "varsayılan profil")
        if target:
            return None, (f"'{target}' adlı çıkış cihazı yok — Audio MIDI Setup'ta "
                          f"oluşturman gerekiyor")
    return None, "eşleşen çıkış profili yok"
