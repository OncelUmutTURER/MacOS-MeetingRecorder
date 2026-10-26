#!/bin/bash
# Bir toplantı kaydını inceler: kanal seviyeleri, zaman içindeki RMS profili, loglar.
#   ./bin/inspect.sh ~/Meetings/2026-08-20-2202
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
# Klasör verilmezse bulunduğun dizin — toplantı klasörünün içindeyken yolu tekrar
# yazmak zorunda kalma.
D="${1:-$PWD}"
if [ ! -f "$D/sys.wav" ] && [ ! -f "$D/mic.wav" ] && ! ls "$D"/*transcript.json >/dev/null 2>&1; then
  echo "HATA: burası bir toplantı klasörü değil:"
  echo "  $D"
  echo
  echo "Ya bir toplantı klasörünün içine gir, ya da yolu ver:"
  echo "  meet inspect ~/Meetings/2026-09-28-1307-auto"
  exit 1
fi

echo "=== $D ==="
ls -la "$D" | grep -vE '^total|^d'

echo
echo "=== kanal ölçümleri ==="
for f in "$D"/*.wav; do
  [ -f "$f" ] || continue
  DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$f")
  LVL=$(ffmpeg -hide_banner -i "$f" -af volumedetect -f null - 2>&1 | grep mean_volume | sed 's/.*mean_volume: //')
  printf '  %-18s %8.1f sn   ortalama %s\n' "$(basename "$f")" "$DUR" "$LVL"
done

for CH in sys mic; do
  [ -f "$D/$CH.wav" ] || continue
  echo
  echo "=== $CH.wav 10 sn RMS profili (-180 = tam dijital sessizlik) ==="
  "$ROOT/venv/bin/python" - "$D/$CH.wav" <<'PY'
import sys, numpy as np, soundfile as sf
a, sr = sf.read(sys.argv[1], dtype="float32", always_2d=True)
a = a.mean(axis=1); w = 10 * sr
for i in range(0, max(len(a) - 1, 1), w):
    seg = a[i:i + w]
    r = float(np.sqrt((seg ** 2).mean()))
    db = 20 * np.log10(max(r, 1e-9))
    bar = "" if db < -60 else "█" * max(1, int((db + 60) / 3))
    print(f"  {i//sr:4d}-{min((i+w)//sr, len(a)//sr):4d} sn: {db:7.1f} dBFS  {bar}")
PY
done

echo
echo "=== guard.log ==="
cat "$D/guard.log" 2>/dev/null || echo "  (yok)"

echo
echo "=== kesilme denetimi (konuşma ortasında kaybolan ses) ==="
for w in "$D/sys.wav" "$D/mic.wav"; do
  [ -f "$w" ] || continue
  "$ROOT/venv/bin/python" "$ROOT/bin/_dropouts.py" "$w" 2>/dev/null \
    | grep -E 'süre|ORTASINDA|✓|~|✗' | sed 's/^/ /'
done
echo
echo "=== kanal / duvar saati kayması ==="
# aresample=async kapatıldıktan sonra tek risk kanalların birbirinden kayması.
# Her kanalın dosya süresini ffmpeg'in gerçek geçen süresiyle karşılaştır:
# ikisi AYNI oranda kısaysa kanallar arası kayma yok demektir.
# Referans sys kanalı: hiç kesilmeden tek parça kaydedilir, ffmpeg logunda gerçek
# geçen süre var. mic parçalı olduğu için kendi logu toplam süreyi vermez; onu sys
# ile karşılaştırıyoruz.
SDUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$D/sys.wav" 2>/dev/null)
MDUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$D/mic.wav" 2>/dev/null)
SEL=$(tr '\r' '\n' < "$D/sys.ffmpeg.log" 2>/dev/null | grep -o 'elapsed=[0-9:.]*' | tail -1 | cut -d= -f2)
python3 - "$SDUR" "$MDUR" "$SEL" <<'PY'
import sys
sd, md, el = sys.argv[1], sys.argv[2], sys.argv[3]
def f(x):
    try: return float(x)
    except Exception: return 0.0
sd, md = f(sd), f(md)
es = 0.0
if el:
    p = [float(x) for x in el.split(':')]
    while len(p) < 3: p.insert(0, 0.0)
    es = p[0]*3600 + p[1]*60 + p[2]
if es:
    print(f"  sys : dosya {sd:8.1f} sn · ffmpeg gerçek {es:8.1f} sn · oran {sd/es*100:6.2f}%")
else:
    print(f"  sys : dosya {sd:8.1f} sn (ffmpeg süresi okunamadı)")
if sd and md:
    diff = md - sd
    print(f"  mic : dosya {md:8.1f} sn · sys'ten fark {diff:+6.1f} sn "
          f"({abs(diff)/sd*100:.2f}%)")
    print("  " + ("✓ kanallar hizalı" if abs(diff) < max(5, sd*0.01)
                  else "⚠ kanallar arası fark büyük — hizalamayı kontrol et"))
PY

echo
echo "=== ffmpeg log başı (açılan cihaz formatları) ==="
grep -h -m2 'Stream #0:0' "$D"/*.log 2>/dev/null || echo "  (yok)"

STAMP=$(basename "$D" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}')
T=""
[ -n "$STAMP" ] && [ -f "$D/$STAMP-transcript.md" ] && T="$D/$STAMP-transcript.md"
[ -z "$T" ] && [ -f "$D/transcript.md" ] && T="$D/transcript.md"
if [ -n "$T" ]; then
  echo
  echo "=== $(basename "$T") ==="
  head -12 "$T"
else
  echo
  echo "=== transkript yok ==="
fi
