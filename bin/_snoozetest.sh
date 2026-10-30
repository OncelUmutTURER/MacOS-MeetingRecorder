#!/bin/bash
# Duraklatmanın GERÇEKTE tuttuğunu doğrular: 60 saniyelik bir kayıt başlatır,
# ortasında "duraklat" der ve kaydın durup DURDUĞUNU, yenisinin BAŞLAMADIĞINI
# ölçer. Birim test sahte durumla çalışıyor; bu gerçek ffmpeg ve gerçek izleyici
# ile sınıyor.
#
# Kullanıcının bildirdiği hata tam olarak buydu: "duraklat dedim, durdu, geri
# sayım başladı, ama yine kayıt başladı."
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
M="$HOME/Meetings"

[ -f "$ROOT/.state.json" ] && { echo "HATA: kayıt sürüyor."; exit 1; }

BEFORE=$(ls -1d "$M"/2* 2>/dev/null | wc -l | tr -d ' ')
echo "başlangıçta $BEFORE toplantı klasörü"

./bin/meet snooze off >/dev/null 2>&1
echo "=== elle kayıt başlatılıyor ==="
./bin/meet start "duraklatma-testi" 2>&1 | tail -3
sleep 20

echo
echo "=== 10 dakikalık duraklatma (süren kaydı da durdurmalı) ==="
./bin/meet snooze 10 2>&1 | tail -3

echo
echo "=== 45 saniye izle: yeni kayıt başlıyor mu ==="
NEW=0
for i in $(seq 1 45); do
  if [ -f "$ROOT/.state.json" ]; then
    NEW=1
    echo "  ✗ $i. saniyede KAYIT VAR: $(./bin/meet status | head -1)"
    break
  fi
  sleep 1
done
[ "$NEW" = "0" ] && echo "  ✓ 45 saniye boyunca yeni kayıt başlamadı"

echo
echo "=== duraklatma hâlâ duruyor mu ==="
./bin/meet snooze

echo
echo "=== elle başlatma duraklatmayı iptal ediyor mu ==="
./bin/meet start "iptal-testi" 2>&1 | grep -i 'duraklat' || echo "  ⚠ iptal mesajı görünmedi"
sleep 3
./bin/meet stop >/dev/null 2>&1
sleep 3
pkill -f 'pipeline.py' 2>/dev/null
./bin/meet snooze

echo
echo "=== temizlik ==="
for d in "$M"/*duraklatma-testi "$M"/*iptal-testi; do
  [ -d "$d" ] && rm -rf "$d" && echo "  silindi: $(basename "$d")"
done
./bin/meet snooze off >/dev/null 2>&1
AFTER=$(ls -1d "$M"/2* 2>/dev/null | wc -l | tr -d ' ')
echo "sonda $AFTER toplantı klasörü (başlangıç $BEFORE)"
[ "$NEW" = "0" ] && echo "SONUÇ: hepsi geçti" || echo "SONUÇ: BAŞARISIZ"
exit "$NEW"
