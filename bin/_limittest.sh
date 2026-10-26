#!/bin/bash
# Süre sınırının GERÇEK bir kayıtla çalıştığını doğrular: 1 dakikalık kayıt
# başlatır, guard'ın kendiliğinden durdurmasını bekler, izleri kontrol eder ve
# test kaydını siler. Birim test guard'ın subprocess/soğuma/bildirim yolunu
# kapsamıyor — bu script onu kapsıyor.
#
# Kullanım: bin/_limittest.sh        (yaklaşık 2 dakika sürer)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MEET="$ROOT/bin/meet"
DIR="$HOME/Meetings"

if [ -f "$ROOT/.state.json" ]; then
  echo "HATA: zaten bir kayıt sürüyor — test yapılmıyor."; exit 1
fi

rm -f "$ROOT/.autostart-cooldown"
echo "=== 1 dakikalık sınırla kayıt başlatılıyor ==="
"$MEET" start "limit-testi" --for 1 2>&1 | tail -6

S=$(ls -1dt "$DIR"/*limit-testi 2>/dev/null | head -1)
echo
echo "klasör: $S"
"$ROOT/venv/bin/python" - <<'PY'
import json, pathlib
d = json.loads(pathlib.Path(".state.json").read_text())
print(f"  state: limit_min={d.get('limit_min')}  stop_at yazıldı={bool(d.get('stop_at'))}")
PY

echo
echo "=== guard'ın durdurması bekleniyor (en çok 110 sn) ==="
for i in $(seq 1 110); do
  [ -f "$ROOT/.state.json" ] || { echo "  kayıt $i. saniyede kendiliğinden durdu"; break; }
  sleep 1
done

echo
if [ -f "$ROOT/.state.json" ]; then
  echo "✗ KAYIT HÂLÂ SÜRÜYOR — sınır çalışmadı"; "$MEET" stop >/dev/null 2>&1
  FAIL=1
else
  echo "✓ kayıt kendiliğinden durdu"
fi

if [ -f "$ROOT/.autostart-cooldown" ]; then
  LEFT=$("$ROOT/venv/bin/python" -c "import time;print(round(float(open('$ROOT/.autostart-cooldown').read())-time.time()))")
  echo "✓ otomatik başlatma soğumada ($LEFT sn) — hemen yeniden başlamayacak"
else
  echo "✗ soğuma dosyası yazılmamış"; FAIL=1
fi

if grep -q "süre sınırı doldu" "$S/guard.log" 2>/dev/null; then
  echo "✓ guard.log gerekçeyi yazdı: $(grep 'süre sınırı doldu' "$S/guard.log" | tail -1)"
else
  echo "✗ guard.log'da gerekçe yok"; FAIL=1
fi

for f in mic.wav sys.wav; do
  if [ -f "$S/$f" ]; then
    D=$(/opt/homebrew/bin/ffprobe -v error -show_entries format=duration -of csv=p=0 "$S/$f" | cut -d. -f1)
    echo "  $f: ${D} sn"
    [ "$D" -gt 120 ] && { echo "  ✗ 2 dakikadan uzun — sınır geç çalışmış"; FAIL=1; }
  fi
done

echo
echo "=== temizlik ==="
pkill -f "pipeline.py.*limit-testi" 2>/dev/null && echo "  test kaydının transkript süreci durduruldu"
sleep 1
rm -f "$ROOT/.autostart-cooldown"
[ -n "${S:-}" ] && [ -d "$S" ] && rm -rf "$S" && echo "  test klasörü silindi: $(basename "$S")"
echo
echo "SONUÇ: ${FAIL:+BAŞARISIZ}${FAIL:-hepsi geçti}"
exit "${FAIL:-0}"
