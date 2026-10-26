#!/bin/bash
# Yayın öncesi gizlilik taraması: depoya SAHNELENEN dosyalarda sır, müşteri adı,
# ürün adı ya da kişisel veri kaldı mı?
#
# Herkese açık bir depoya bir kez sır gönderildiğinde geçmişten temizlemek zordur;
# bu yüzden push'tan ÖNCE çalışır ve bulursa sıfırdan farklı kodla çıkar.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
export PATH=/opt/homebrew/bin:/usr/bin:/bin:$PATH

echo "=== sahnelenen dosya sayısı ==="
git diff --cached --name-only | wc -l

echo
echo "=== gizli kalması gerekenler depoya girmiyor mu ==="
# DİKKAT: `git check-ignore` index'i de hesaba katıyor — bir dosya bir kez
# izlenmeye başladıysa .gitignore'da olsa bile "yok sayılmıyor" der ve asıl soru
# zaten budur. Bu yüzden iki şeyi birden sınıyoruz: index'te mi, ve desen tutuyor mu.
FAIL=0
for f in .env config.yaml glossary.md glossary-candidates.md CLAUDE.md \
         TEST-OKUMA-METNI.md venv .state.json .autostart-enabled; do
  if [ ! -e "$f" ]; then
    echo "  · $f (dosya yok)"
  elif git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    echo "  ✗ $f İNDEKSTE — git rm --cached \"$f\""; FAIL=1
  elif git check-ignore --no-index -q "$f" 2>/dev/null; then
    echo "  ✓ $f"
  else
    echo "  ✗ $f .gitignore ile eşleşmiyor"; FAIL=1
  fi
done

echo
echo "=== sahnelenen içerikte hassas kelime taraması ==="
# BU LİSTE DEPOYA GİRMEZ. İlk sürümde aranacak adlar (ürün, müşteri, cihaz) doğrudan
# bu dosyanın içindeydi ve herkese açık depoda yayınlandı — yani tarayıcının kendisi,
# gizlenmesi gereken adların listesini sızdırdı. Artık kuruma özel desenler
# `.publishcheck-patterns` dosyasından okunuyor (.gitignore'da, her satıra bir desen).
# Burada yalnızca HERKES için geçerli olan anahtar/token kalıpları duruyor.
# DİKKAT: liste boşlukla bölünüyor, yani desenlerin içinde BOŞLUK OLAMAZ.
# ("-----BEGIN RSA PRIVATE KEY-----" yazınca grep parçalanmış desenle patlıyor.)
PATTERNS='hf_[A-Za-z0-9]{12} sk-ant-[A-Za-z0-9_-]{12} ghp_[A-Za-z0-9]{12}
          AKIA[A-Z0-9]{12} BEGIN.?[A-Z]*.?PRIVATE.?KEY'
LOCAL_PATTERNS=".publishcheck-patterns"
if [ -f "$LOCAL_PATTERNS" ]; then
  EXTRA=$(grep -vE '^\s*(#|$)' "$LOCAL_PATTERNS" | tr '\n' ' ')
  PATTERNS="$PATTERNS $EXTRA"
  echo "  ($(grep -cvE '^\s*(#|$)' "$LOCAL_PATTERNS") yerel desen $LOCAL_PATTERNS dosyasından eklendi)"
else
  echo "  (yerel desen dosyası yok: $LOCAL_PATTERNS — kuruma özel adları oraya yaz)"
fi
for p in $PATTERNS; do
  # Kendi desen listesini bulmasın: tarayıcının kendi dosyası hariç tutuluyor.
  hits=$(git diff --cached -U0 -- . ":(exclude)bin/_publishcheck.sh" \
         | grep -nE "^\+.*$p" | grep -v '^+++' | head -3)
  if [ -n "$hits" ]; then
    echo "  ✗ '$p' bulundu:"; echo "$hits" | sed 's/^/      /'; FAIL=1
  fi
done
[ "$FAIL" = "0" ] && echo "  ✓ hassas kelime yok"

echo
echo "=== yazar adı nerede geçiyor (bilerek olmalı) ==="
git diff --cached --name-only | while read -r f; do
  [ -f "$f" ] || continue
  if grep -qi 'oncel' "$f" 2>/dev/null; then echo "  · $f"; fi
done

echo
echo "=== büyük dosyalar (>1 MB sahnelenmemeli) ==="
git diff --cached --name-only | while read -r f; do
  [ -f "$f" ] || continue
  sz=$(stat -f%z "$f" 2>/dev/null || echo 0)
  [ "$sz" -gt 1000000 ] && echo "  ✗ $f ($((sz/1000000)) MB)"
done
echo "  (üstte satır yoksa temiz)"

echo
if [ "$FAIL" = "0" ]; then echo "SONUÇ: yayına uygun"; else echo "SONUÇ: DÜZELTİLMELİ"; fi
exit "$FAIL"
