#!/bin/bash
# Toplantı klasörlerinde bozulma ve gereksiz dosya denetimi.
#
#   meet audit           sadece raporla
#   meet audit --fix     güvenli temizliği onay alarak uygula
#
# Neyi arıyor:
#   1. mic.wav bütünlüğü — parçaların toplam süresine uyuyor mu (paralel süreç
#      çakışmasında bozulmuş olabilir)
#   2. bozuk/okunamayan wav dosyaları
#   3. artık mic.partNN.wav dosyaları — mic.wav üretildikten sonra gereksizler
#   4. sahipsiz progress.json, .tmp dosyaları, sıfır baytlıklar
#   5. dev ffmpeg logları (parça rotasyonu çok olduysa MB'larca olabiliyor)
#   6. HAYALET ffmpeg süreçleri — bitmiş toplantılara yazmaya devam edenler.
#      Mikrofonu açık tuttukları için sonraki kayıtları bozarlar; bir ölçümde
#      9 güne kadar yaşayan 10 tane bulundu.
#   7. eski adlı çıktılar — düz "transcript.md" / "action-items.md". Çıktılar artık
#      toplantı damgasını adında taşıyor; --fix bunları yeni ada taşır.
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
M="$HOME/Meetings"
FIX=0
for a in "$@"; do case "$a" in --fix) FIX=1 ;; *) M="$a" ;; esac; done

dur() { ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$1" 2>/dev/null || echo 0; }
mb()  { python3 -c "import os,sys; print(f'{os.path.getsize(sys.argv[1])/1e6:.1f}')" "$1" 2>/dev/null || echo 0; }

BROKEN=(); PARTS=(); JUNK=(); BIGLOG=(); OLDNAMED=(); STALESKIP=(); SPLITBAK=0
PARTS_MB=0; JUNK_MB=0; LOG_MB=0

echo "Denetim: $M"
echo

for d in "$M"/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  issues=""

  # --- 1/2. wav butunlugu -------------------------------------------------
  for w in "$d"mic.wav "$d"sys.wav; do
    [ -f "$w" ] || continue
    if ! ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$w" >/dev/null 2>&1; then
      issues="$issues  ✗ $(basename "$w") OKUNAMIYOR"
      BROKEN+=("$w")
    fi
  done

  # --- 3. artik mic parcalari ---------------------------------------------
  if [ -f "$d/mic.wav" ]; then
    pcount=$(ls "$d"mic.part*.wav 2>/dev/null | wc -l | tr -d ' ')
    if [ "$pcount" -gt 0 ]; then
      psum=0
      for p in "$d"mic.part*.wav; do
        psum=$(python3 -c "print($psum + $(dur "$p"))")
        PARTS+=("$p"); PARTS_MB=$(python3 -c "print(round($PARTS_MB + $(mb "$p"),1))")
      done
      mdur=$(dur "$d/mic.wav")
      # birlestirme sessizlikle doldurdugu icin mic.wav >= parcalarin toplami olmali
      short=$(python3 -c "print(1 if $mdur < $psum - 5 else 0)")
      [ "$short" = "1" ] && issues="$issues
  ⚠ mic.wav ($(printf '%.0f' "$mdur") sn) parçalar toplamından ($(printf '%.0f' "$psum") sn) KISA — birleştirme eksik olabilir"
    fi
  fi

  # --- 4. artiklar ---------------------------------------------------------
  for j in "$d"progress.json "$d"*.tmp; do
    [ -f "$j" ] || continue
    JUNK+=("$j"); JUNK_MB=$(python3 -c "print(round($JUNK_MB + $(mb "$j"),1))")
  done
  while IFS= read -r z; do
    [ -n "$z" ] && JUNK+=("$z")
  done < <(find "$d" -maxdepth 1 -type f -size 0 2>/dev/null)

  # --- 5. dev loglar -------------------------------------------------------
  # ffmpeg ilerleme logları ZAMAN HARİTASININ kaynağı (timeline.py): içlerindeki
  # `size=` + `time=` çiftleri dosya zamanı ile duvar saati arasındaki eşlemeyi
  # veriyor. Harita bir kez `timeline.json`'a yazıldıysa log gitmeli; yazılmadan
  # silersek transkript yeniden çıkarıldığında zaman ekseni düzeltilemez.
  # (Ölçüldü: parça logları silinmiş bir kayıtta mic haritası 63 dakika kayıyordu.)
  for l in "$d"*.log; do
    [ -f "$l" ] || continue
    if [ ! -f "$d/timeline.json" ]; then continue; fi
    big=$(python3 -c "import os;print(1 if os.path.getsize('$l')>2_000_000 else 0)")
    if [ "$big" = "1" ]; then
      BIGLOG+=("$l"); LOG_MB=$(python3 -c "print(round($LOG_MB + $(mb "$l"),1))")
    fi
  done

  # --- 7. eski adli ciktilar ----------------------------------------------
  stamp=$(echo "$n" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}')
  if [ -n "$stamp" ]; then
    for kind in transcript.md transcript.json action-items.md; do
      [ -f "$d$kind" ] || continue
      OLDNAMED+=("$d$kind")
      issues="$issues
  · $kind → $stamp-$kind olmalı (paylaşırken ad çakışmasın diye)"
    done
  fi

  # --- 8. bolunmus kaydin yedek sesi ---------------------------------------
  # meet split sonrasi orijinal ses yedek olarak duruyor. Silmek kullanicinin
  # karari; burada sadece ne kadar yer tuttugunu hatirlatiyoruz.
  # Yarımları silinmiş bölme kaydı: .skip toplantıyı rapor listesinden gizli tutar
  # ama gösterdiği klasörler artık yok — içerik sessizce kaybolur.
  if [ -f "$d/.skip" ] && grep -q 'bölündü' "$d/.skip" 2>/dev/null; then
    # IFS= ŞART: varsayılan IFS baştaki boşlukları kırpar ve girintili yarım
    # adları hiç eşleşmez — ilk sürümde tam olarak bu oldu, kontrol sessizce
    # her zaman "sorun yok" dedi.
    missing=0; halves=0
    while IFS= read -r h; do
      case "$h" in
        "  "*) name="${h#  }"
               halves=$((halves+1))
               [ -d "$M/$name" ] || missing=$((missing+1)) ;;
      esac
    done < "$d/.skip"
    if [ "$halves" -gt 0 ] && [ "$missing" -eq "$halves" ]; then
      STALESKIP+=("$d")
      issues="$issues
  ⚠ bölme yarımları silinmiş ama .skip duruyor — bu toplantı rapor listesinde
    görünmüyor. --fix .skip'i kaldırır (ses ve transkript zaten burada)."
    fi
  fi

  if [ -f "$d/.skip" ] && grep -q 'bölündü' "$d/.skip" 2>/dev/null; then
    sz=0
    for w in "$d"mic.wav "$d"sys.wav; do
      [ -f "$w" ] && sz=$(python3 -c "print(round($sz + $(mb "$w"),1))")
    done
    if [ "$(python3 -c "print(1 if $sz>0 else 0)")" = "1" ]; then
      SPLITBAK=$(python3 -c "print(round($SPLITBAK + $sz,1))")
      issues="$issues
  · bölünmüş kayıt, yedek ses $sz MB — yer açmak için:
      meet split \"${d%/}\" --drop-original-audio"
    fi
  fi

  [ -n "$issues" ] && printf '%s\n%s\n' "$n" "$issues"
done

# --- 6. hayalet ffmpeg surecleri ------------------------------------------
GHOSTS=()
while IFS= read -r line; do
  [ -n "$line" ] && GHOSTS+=("$line")
done < <(ps -Ao pid=,etime=,args= | grep '[f]fmpeg' | grep -F "$M" \
         | awk '{print $1"\t"$2}')

if [ "${#GHOSTS[@]}" -gt 0 ]; then
  # aktif kayit varsa onun surecleri hayalet degil
  ACTIVE=""
  ST="$ROOT/.state.json"
  [ -f "$ST" ] && ACTIVE=$(python3 -c "import json;print(json.load(open('$ST'))['session_dir'])" 2>/dev/null)
  KEEP=()
  for g in "${GHOSTS[@]}"; do
    pid="${g%%$'\t'*}"
    args=$(ps -p "$pid" -o args= 2>/dev/null)
    if [ -n "$ACTIVE" ] && [[ "$args" == *"$ACTIVE"* ]]; then continue; fi
    KEEP+=("$g")
  done
  GHOSTS=("${KEEP[@]}")
fi

if [ "${#GHOSTS[@]}" -gt 0 ]; then
  echo "HAYALET SÜREÇLER"
  for g in "${GHOSTS[@]}"; do
    pid="${g%%$'\t'*}"; et="${g##*$'\t'}"
    tgt=$(ps -p "$pid" -o args= 2>/dev/null | tr ' ' '\n' | grep -F "$M" | head -1)
    printf '  ✗ pid %-7s yaş %-14s %s\n' "$pid" "$et" "$(basename "$(dirname "$tgt")")/$(basename "$tgt")"
  done
  echo "  → mikrofonu açık tutuyorlar, sonraki kayıtlar sessiz çıkabilir"
  echo
fi

echo "────────────────────────────────────────────────────────────"
printf 'hayalet ffmpeg      : %s süreç\n' "${#GHOSTS[@]}"
printf 'eski adlı çıktı     : %s dosya\n' "${#OLDNAMED[@]}"
printf 'bölünmüş yedek ses  : %s MB (elle silinir, --fix dokunmaz)\n' "$SPLITBAK"
printf 'artakalan .skip     : %s toplantı (yarımları silinmiş)\n' "${#STALESKIP[@]}"
printf 'okunamayan wav      : %s\n' "${#BROKEN[@]}"
printf 'artık mic parçası   : %s dosya, %s MB\n' "${#PARTS[@]}" "$PARTS_MB"
printf 'artık/boş dosya     : %s dosya, %s MB\n' "${#JUNK[@]}" "$JUNK_MB"
printf 'dev ffmpeg logu     : %s dosya, %s MB\n' "${#BIGLOG[@]}" "$LOG_MB"
TOTAL=$(python3 -c "print(round($PARTS_MB + $JUNK_MB + $LOG_MB,1))")
printf 'geri kazanılabilir  : %s MB\n' "$TOTAL"

if [ "${#BROKEN[@]}" -gt 0 ]; then
  echo
  echo "⚠ OKUNAMAYAN DOSYALAR — bunlar otomatik silinmez, elle bak:"
  printf '   %s\n' "${BROKEN[@]}"
fi

if [ "$FIX" != "1" ]; then
  echo
  [ "${#GHOSTS[@]}" -gt 0 ] && echo "Hayalet süreçleri öldürmek için de:  meet audit --fix"
  [ "${#OLDNAMED[@]}" -gt 0 ] && echo "Eski adlı çıktıları damgalı ada taşımak için de:  meet audit --fix"
  echo "Temizlemek için:  meet audit --fix"
  echo "(mic.wav'ı olan toplantıların mic.partNN.wav dosyaları, artıklar ve dev loglar"
  echo " silinir. Ses kayıtlarının kendisine —mic.wav / sys.wav— dokunulmaz.)"
  exit 0
fi

NFILES=$(( ${#PARTS[@]} + ${#JUNK[@]} + ${#BIGLOG[@]} ))
if [ "$NFILES" -gt 0 ]; then
  echo
  echo "SİLİNECEK: ${#PARTS[@]} parça + ${#JUNK[@]} artık + ${#BIGLOG[@]} log  ≈ $TOTAL MB"
  printf 'Onaylıyor musun? (yes yaz): '
  read -r ans
  if [ "$ans" = "yes" ]; then
    DEL=0
    for f in "${PARTS[@]}" "${JUNK[@]}" "${BIGLOG[@]}"; do
      [ -f "$f" ] && rm -f "$f" && DEL=$((DEL+1))
    done
    echo "✓ $DEL dosya silindi, ~$TOTAL MB yer açıldı."
  else
    echo "Dosya silme iptal edildi."
  fi
fi

# --- hayalet surecler ------------------------------------------------------
if [ "${#GHOSTS[@]}" -gt 0 ]; then
  K=0
  for g in "${GHOSTS[@]}"; do
    pid="${g%%$'\t'*}"
    kill -INT "$pid" 2>/dev/null
  done
  sleep 3
  for g in "${GHOSTS[@]}"; do
    pid="${g%%$'\t'*}"
    if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null; fi
    kill -0 "$pid" 2>/dev/null || K=$((K+1))
  done
  echo "✓ $K hayalet ffmpeg süreci öldürüldü."
fi

# --- yarimlari silinmis bolme kayitlarinin .skip'ini kaldir ----------------
if [ "${#STALESKIP[@]}" -gt 0 ]; then
  S=0
  for d in "${STALESKIP[@]}"; do
    rm -f "$d/.skip" && S=$((S+1))
    echo "  ✓ $(basename "$d"): .skip kaldırıldı, rapor listesine geri döndü"
  done
  echo "✓ $S artakalan .skip temizlendi."
fi

# --- eski adli ciktilari damgali ada tasi ----------------------------------
if [ "${#OLDNAMED[@]}" -gt 0 ]; then
  R=0
  for f in "${OLDNAMED[@]}"; do
    [ -f "$f" ] || continue
    dir=$(dirname "$f"); base=$(basename "$f")
    stamp=$(basename "$dir" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}')
    [ -n "$stamp" ] || continue
    new="$dir/$stamp-$base"
    if [ -f "$new" ]; then
      if cmp -s "$f" "$new"; then rm -f "$f"; R=$((R+1))
      else echo "  ⚠ $(basename "$dir")/$base ile $stamp-$base FARKLI — elle bak"; fi
    else
      mv "$f" "$new" && R=$((R+1))
    fi
  done
  echo "✓ $R çıktı dosyası damgalı adına alındı."
fi
