#!/bin/bash
# Zaman ekseni düzeltmesinin GERÇEK bir kayıtta kurulduğunu doğrular.
# 90 saniyelik bir kayıt alır, mic.parts.json ve harita kuruluyor mu bakar,
# sonra test kaydını siler. (Birim test sentetik logla çalışıyor; bu, ffmpeg'in
# gerçekten o satırları yazdığını ve parça haritasının üretildiğini sınar.)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SECS="${1:-90}"

[ -f "$ROOT/.state.json" ] && { echo "HATA: kayıt sürüyor."; exit 1; }

./bin/meet snooze 5 >/dev/null 2>&1
trap './bin/meet snooze off >/dev/null 2>&1' EXIT

./bin/meet start "zaman-testi" 2>&1 | tail -3
sleep "$SECS"
./bin/meet stop 2>&1 | grep -iE 'parça|birleştir|transkript' | head -4
sleep 3
pkill -f 'pipeline.py' 2>/dev/null

S=$(ls -1dt "$HOME"/Meetings/*zaman-testi 2>/dev/null | head -1)
echo "klasör: $S"
echo "--- mic.parts.json ---"
head -c 400 "$S/mic.parts.json" 2>/dev/null || echo "YOK"
echo
./venv/bin/python - "$S" <<'PY'
import sys
sys.path.insert(0, 'src')
from pathlib import Path
import timeline as TL
from common import load_config
s = Path(sys.argv[1])
m = TL.build(s, load_config())
print("haritalar:", sorted(m) or "YOK")
for ch, p in m.items():
    loss = (1 - p[-1][0] / p[-1][1]) * 100 if p[-1][1] else 0
    print(f"  {ch}: dosya {p[-1][0]:7.1f} sn = duvar {p[-1][1]:7.1f} sn "
          f"(+{p[-1][1]-p[-1][0]:.1f} sn, %{loss:.1f} kayıp)")
print("SONUÇ:", "hepsi geçti" if {"mic", "sys"} <= set(m) else "BAŞARISIZ — iki kanal da gerekli")
PY
rm -rf "$S" && echo "test klasörü silindi"
