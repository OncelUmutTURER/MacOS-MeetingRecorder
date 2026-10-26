#!/bin/bash
# sys kanalındaki ~103 ms'lik kesilmelerin kaynağını bulur.
#
#   _droptest.sh <cikis_cihazi> <etiket> [saniye_per_test]
#
# BlackHole'a kesintisiz bir sinüs çalar ve dört ffmpeg ayarını SIRAYLA dener.
# Ton hiç susmadığı için kayıttaki her sıfır-koşusu kayıptır; doğal duraklama yok.
#
#   A  mevcut ayar   : -use_wallclock_as_timestamps + aresample=async=1000
#   B  wallclock yok : aresample=async=1000
#   C  aresample yok : -use_wallclock_as_timestamps + düz -ar
#   D  ham           : ikisi de yok
#
# SIRAYLA çalıştırmak şart: aynı avfoundation cihazını iki ffmpeg aynı anda
# açamıyor, ikincisi sessizlik alıyor. (İlk denemede dördü paralel çalıştırıldı
# ve dördü de tamamen sessiz çıktı — ölçüm değil, çakışma.)
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
PY="$ROOT/venv/bin/python"

DEV="$1"; TAG="$2"; SECS="${3:-25}"
OUT="${TMPDIR:-/tmp}/droptest-$TAG"
rm -rf "$OUT"; mkdir -p "$OUT"

PREV=$(SwitchAudioSource -c -t output)
cleanup() { pkill -f 'lavfi -i sine' 2>/dev/null
            SwitchAudioSource -s "$PREV" -t output >/dev/null 2>&1; }
trap cleanup EXIT

echo "== $TAG ==  cikis: $DEV  (oncesi: $PREV)"
SwitchAudioSource -s "$DEV" -t output >/dev/null 2>&1 || { echo "  cihaz ayarlanamadi"; exit 1; }
sleep 3

IDX=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i '' 2>&1 \
      | sed -n '/audio devices/,$p' | grep -m1 'BlackHole 2ch' \
      | sed -E 's/.*\[([0-9]+)\] BlackHole 2ch.*/\1/')
[ -n "$IDX" ] || { echo "  BlackHole bulunamadi"; exit 1; }
echo "  BlackHole giris indeksi: $IDX"

# kesintisiz ton — BlackHole'a giden tek kaynak
ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=48000" \
       -t $((SECS * 5 + 40)) -af volume=0.25 -f audiotoolbox - >/dev/null 2>&1 &
sleep 3

# once seviye kontrolu: ton gercekten BlackHole'a ulasiyor mu
ffmpeg -hide_banner -loglevel error -y -f avfoundation -i ":$IDX" -t 4 \
       -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/probe.wav" >/dev/null 2>&1
LVL=$(ffmpeg -hide_banner -i "$OUT/probe.wav" -af volumedetect -f null - 2>&1 \
      | grep mean_volume | awk '{print $5}')
echo "  ton seviyesi: ${LVL:-?} dB"
case "$LVL" in
  ""|-9*) echo "  X ton BlackHole'a ulasmiyor, test anlamsiz"; exit 1 ;;
esac

run() {
  echo "  -> $1 kaydediliyor (${SECS}s)"
  shift
  ffmpeg -hide_banner -loglevel error -y "$@" -t "$SECS" -ac 1 -c:a pcm_s16le "$1x" \
    >/dev/null 2>&1
}

ffmpeg -hide_banner -loglevel error -y -use_wallclock_as_timestamps 1 -f avfoundation \
  -i ":$IDX" -af "aresample=async=1000:first_pts=0:osr=16000" -t "$SECS" -ac 1 \
  -c:a pcm_s16le "$OUT/A.wav" >/dev/null 2>&1; echo "  A bitti"
ffmpeg -hide_banner -loglevel error -y -f avfoundation \
  -i ":$IDX" -af "aresample=async=1000:first_pts=0:osr=16000" -t "$SECS" -ac 1 \
  -c:a pcm_s16le "$OUT/B.wav" >/dev/null 2>&1; echo "  B bitti"
ffmpeg -hide_banner -loglevel error -y -use_wallclock_as_timestamps 1 -f avfoundation \
  -i ":$IDX" -ar 16000 -t "$SECS" -ac 1 \
  -c:a pcm_s16le "$OUT/C.wav" >/dev/null 2>&1; echo "  C bitti"
ffmpeg -hide_banner -loglevel error -y -f avfoundation \
  -i ":$IDX" -ar 16000 -t "$SECS" -ac 1 \
  -c:a pcm_s16le "$OUT/D.wav" >/dev/null 2>&1; echo "  D bitti"

cleanup; trap - EXIT
echo

"$PY" - "$OUT" <<'PY'
import sys, wave, numpy as np
from pathlib import Path
out = Path(sys.argv[1])
names = {"A": "wallclock + aresample (MEVCUT)", "B": "aresample (wallclock yok)",
         "C": "wallclock (aresample yok)", "D": "ham (ikisi de yok)"}
print(f"  {'ayar':<34} {'sure':>7} {'kesilme':>8} {'kayip':>8} {'medyan':>9}")
print("  " + "-" * 72)
for k in "ABCD":
    f = out / f"{k}.wav"
    if not f.exists():
        print(f"  {names[k]:<34}   (kayit olusmadi)"); continue
    with wave.open(str(f), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16)
    dur = len(a) / sr
    z = (a == 0).view(np.int8)
    d = np.diff(np.concatenate(([0], z, [0])))
    s, e = np.where(d == 1)[0], np.where(d == -1)[0]
    keep = (e - s) >= int(sr * 0.01)
    s, e = s[keep], e[keep]
    lost = (e - s).sum() / sr
    med = np.median(e - s) / sr * 1000 if len(s) else 0
    rate = len(s) / (dur / 60) if dur else 0
    print(f"  {names[k]:<34} {dur:6.1f}s {len(s):8d} {lost:7.2f}s {med:8.0f}ms"
          f"   ({rate:.1f}/dk)")
PY
