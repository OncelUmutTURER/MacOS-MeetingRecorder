#!/bin/bash
# Raporu bekleyen toplantıları süre ve içerik yoğunluğuyla listeler.
# Yanlış tetiklenmiş kısa kayıtları ayıklamak için.
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
CLEAN=0
M="$HOME/Meetings"
for a in "$@"; do
  case "$a" in
    --clean) CLEAN=1 ;;
    *) M="$a" ;;
  esac
done
MIN_LINES=40

printf '%-26s %8s %7s %7s  %s\n' "TOPLANTI" "SÜRE" "SATIR" "KONUŞ." "DURUM"
printf '%s\n' "────────────────────────────────────────────────────────────────────────"

TOTAL=0; JUNK=0; REAL=0; SKIPPED=0; JUNKDIRS=()
# Çıktı adları toplantı damgasını taşır (2026-09-09-1110-transcript.md); eski
# kayıtlarda düz transcript.md olabilir, ikisini de kabul et.
find_out() {  # find_out <klasör> <transcript.md|action-items.md>
  local d="$1" kind="$2" stamp
  stamp=$(basename "$d" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}')
  [ -n "$stamp" ] && [ -f "$d/$stamp-$kind" ] && { echo "$d/$stamp-$kind"; return 0; }
  [ -f "$d/$kind" ] && { echo "$d/$kind"; return 0; }
  return 1
}

for d in "$M"/*/; do
  T=$(find_out "$d" transcript.md) || continue
  find_out "$d" action-items.md >/dev/null && continue
  # .skip: kullanıcı bu toplantının raporunu istemiyor
  if [ -f "$d/.skip" ]; then SKIPPED=$((SKIPPED+1)); continue; fi
  n=$(basename "$d")
  # Bölünmüş yarımlarda ses dosyası yok (orijinal klasörde duruyor); süreyi
  # transkriptin meta'sından al.
  dur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$d/sys.wav" 2>/dev/null || echo 0)
  if [ "${dur%%.*}" = "0" ] || [ -z "$dur" ]; then
    J=$(find_out "$d" transcript.json) && dur=$(python3 -c "
import json,sys
try: print(json.load(open('$J'))['meta'].get('duration',0))
except Exception: print(0)" 2>/dev/null)
  fi
  mins=$(python3 -c "print(f'{float('$dur')/60:.0f} dk')" 2>/dev/null || echo '?')
  lines=$(grep -c '^\*\*\[' "$T" 2>/dev/null || echo 0)
  spk=$(grep -o '^\*\*\[[0-9:]*\] [^:]*:' "$T" 2>/dev/null | sed 's/.*] //;s/:$//' | sort -u | wc -l | tr -d ' ')
  TOTAL=$((TOTAL+1))
  if [ "$lines" -lt $MIN_LINES ]; then
    status="⚠ içerik az — yanlış tetikleme olabilir"; JUNK=$((JUNK+1)); JUNKDIRS+=("$d")
  else
    status="gerçek toplantı"; REAL=$((REAL+1))
  fi
  printf '%-26s %8s %7s %7s  %s\n' "$n" "$mins" "$lines" "$spk" "$status"
done

printf '\n%s\n' "────────────────────────────────────────────────────────────────────────"
echo "toplam $TOTAL bekliyor · $REAL gerçek görünüyor · $JUNK içeriği az · $SKIPPED atlandı"
[ "$SKIPPED" -gt 0 ] && echo "(atlananları geri getirmek için ilgili klasörden .skip dosyasını sil)"

if [ "$JUNK" -eq 0 ]; then
  echo
  echo "Silinecek bir şey yok."
  exit 0
fi

SIZE=$(du -ch "${JUNKDIRS[@]}" 2>/dev/null | tail -1 | cut -f1)
if [ "$CLEAN" != "1" ]; then
  echo
  echo "İçeriği az olan $JUNK kaydı ($SIZE) silmek için:"
  echo "  $ROOT/bin/pending.sh --clean"
  exit 0
fi

echo
echo "SİLİNECEK ($SIZE):"
printf '  %s\n' "${JUNKDIRS[@]}"
printf '\nBu klasörler ses dosyalarıyla birlikte KALICI olarak silinecek.\nOnaylıyor musun? (yes yaz): '
read -r ans
if [ "$ans" = "yes" ]; then
  rm -rf "${JUNKDIRS[@]}"
  echo "✓ $JUNK kayıt silindi ($SIZE yer açıldı)."
else
  echo "İptal edildi, hiçbir şey silinmedi."
fi
