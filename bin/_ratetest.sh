#!/bin/bash
# Örnek kaybı GİRİŞ HIZI pazarlığından mı geliyor?
#
# Ölçüm (2026-10-21): yakalanan dosya duvar saatinden kısa çıkıyor — mikrofonda
# %8, BlackHole'da %3-4. Mikrofondaki %8, 44100/48000 oranına (%8.1) şüpheli
# derecede yakın: avfoundation bir hız bildirip başka hızda örnek üretiyor
# olabilir. Öyleyse girişe doğru hızı dayatmak kaybı sıfırlar ve `aresample`
# gerekmez.
#
# Her varyant 20 saniye, SIRAYLA (aynı cihazı iki ffmpeg aynı anda açamaz).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
P=/opt/homebrew/bin
DEV="${1:-4}"          # 4 = MacBook Pro Microphone, 2 = BlackHole
SECS="${2:-20}"

"$ROOT/bin/meet" snooze 4 >/dev/null 2>&1     # test mikrofonu açıyor
trap '"$ROOT/bin/meet" snooze off >/dev/null 2>&1' EXIT

run() {
  local label="$1"; shift
  local t0 t1
  t0=$(python3 -c 'import time;print(time.time())')
  $P/ffmpeg -hide_banner -y "$@" -ac 1 -c:a pcm_s16le /tmp/rt.wav >/tmp/rt.err 2>&1 &
  local pid=$!
  sleep "$SECS"
  kill -INT $pid 2>/dev/null; wait $pid 2>/dev/null
  t1=$(python3 -c 'import time;print(time.time())')
  echo "-- $label"
  grep -m1 'Stream #0:0' /tmp/rt.err | sed 's/.*Audio: /   giriş : /' | cut -c1-58
  grep -iE 'error|invalid|not supported|could not' /tmp/rt.err | head -2 | sed 's/^/   HATA: /'
  python3 - "$t0" "$t1" <<'PY'
import sys, wave
t0, t1 = float(sys.argv[1]), float(sys.argv[2])
try:
    w = wave.open('/tmp/rt.wav', 'rb')
    d = w.getnframes() / w.getframerate()
    wall = t1 - t0
    print(f"   sonuç: dosya {d:.2f} sn / duvar {wall:.2f} sn  →  {(d-wall)/wall*100:+.2f}%")
except Exception as e:
    print("   sonuç: dosya okunamadı:", e)
PY
  sleep 2
}

echo "cihaz :$DEV · her varyant ${SECS} sn"
run "bugünkü (hız dayatılmamış)" -use_wallclock_as_timestamps 1 -f avfoundation -i ":$DEV" -ar 16000
run "girişe 48000 dayatılmış"    -use_wallclock_as_timestamps 1 -f avfoundation -ar 48000 -i ":$DEV" -ar 16000
run "girişe 44100 dayatılmış"    -use_wallclock_as_timestamps 1 -f avfoundation -ar 44100 -i ":$DEV" -ar 16000
run "girişe 16000 dayatılmış"    -use_wallclock_as_timestamps 1 -f avfoundation -ar 16000 -i ":$DEV" -ar 16000
