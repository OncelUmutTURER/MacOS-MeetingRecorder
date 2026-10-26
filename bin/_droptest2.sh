#!/bin/bash
# Kesilmeyi üretmeyen ama hizalamayı da bozmayan aresample ayarını arar.
#
# Bulgu: aresample'ın min_hard_comp varsayılanı 0.1 sn. Zaman damgası ile ses
# verisi arasındaki fark bunu aşınca ffmpeg 100 ms SESSİZLİK enjekte ediyor —
# ölçülen boşluklar tam 103-106 ms. Filtreyi tamamen kaldırmak kesilmeyi bitiriyor
# ama kanallar arası kaymayı da serbest bırakıyor, o yüzden ara bir ayar gerek.
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
PY="$ROOT/venv/bin/python"
SECS="${1:-30}"
OUT="${TMPDIR:-/tmp}/droptest2"; rm -rf "$OUT"; mkdir -p "$OUT"

PREV=$(SwitchAudioSource -c -t output)
cleanup() { pkill -f 'lavfi -i sine' 2>/dev/null
            SwitchAudioSource -s "$PREV" -t output >/dev/null 2>&1; }
trap cleanup EXIT
SwitchAudioSource -s 'BlackHole 2ch' -t output >/dev/null 2>&1
sleep 3
IDX=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i '' 2>&1 \
      | sed -n '/audio devices/,$p' | grep -m1 'BlackHole 2ch' \
      | sed -E 's/.*\[([0-9]+)\] BlackHole 2ch.*/\1/')
[ -n "$IDX" ] || { echo "BlackHole yok"; exit 1; }
ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=48000" \
       -t $((SECS * 6 + 40)) -af volume=0.25 -f audiotoolbox - >/dev/null 2>&1 &
sleep 3

cap() {  # cap <ad> <af-degeri veya BOS>
  local name="$1" af="$2"
  if [ -n "$af" ]; then
    ffmpeg -hide_banner -loglevel error -y -use_wallclock_as_timestamps 1 \
      -f avfoundation -i ":$IDX" -af "$af" -t "$SECS" -ac 1 -c:a pcm_s16le \
      "$OUT/$name.wav" >/dev/null 2>&1
  else
    ffmpeg -hide_banner -loglevel error -y -use_wallclock_as_timestamps 1 \
      -f avfoundation -i ":$IDX" -ar 16000 -t "$SECS" -ac 1 -c:a pcm_s16le \
      "$OUT/$name.wav" >/dev/null 2>&1
  fi
  echo "  $name bitti"
}

cap A "aresample=async=1000:first_pts=0:osr=16000"
cap E "aresample=async=1:first_pts=0:osr=16000"
cap F "aresample=async=1000:min_hard_comp=1.0:first_pts=0:osr=16000"
cap G "aresample=async=1000:min_comp=0.001:max_soft_comp=0.02:min_hard_comp=1.0:first_pts=0:osr=16000"
cap H ""
cleanup; trap - EXIT
echo

"$PY" - "$OUT" "$SECS" <<'PY'
import sys, wave, numpy as np
from pathlib import Path
out, want = Path(sys.argv[1]), float(sys.argv[2])
names = {"A": "async=1000 (MEVCUT)",
         "E": "async=1  (sadece baslangic)",
         "F": "async=1000 + min_hard_comp=1.0",
         "G": "async=1000 + yumusak telafi",
         "H": "aresample yok (kontrol)"}
print(f"  {'ayar':<34} {'sure':>7} {'sapma':>7} {'kesilme':>8} {'medyan':>8}")
print("  " + "-" * 70)
for k in "AEFGH":
    f = out / f"{k}.wav"
    if not f.exists():
        print(f"  {names[k]:<34}  (yok)"); continue
    with wave.open(str(f), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16)
    dur = len(a) / sr
    z = (a == 0).view(np.int8)
    d = np.diff(np.concatenate(([0], z, [0])))
    s, e = np.where(d == 1)[0], np.where(d == -1)[0]
    keep = (e - s) >= int(sr * 0.01)
    s, e = s[keep], e[keep]
    med = np.median(e - s) / sr * 1000 if len(s) else 0
    print(f"  {names[k]:<34} {dur:6.1f}s {dur-want:+6.2f}s {len(s):8d} {med:7.0f}ms")
print()
print("  sapma = dosya suresi - istenen sure. Buyuk negatif sapma, ses akisinin")
print("  duvar saatinden geri kaldigini gosterir (hizalama icin onemli).")
PY
