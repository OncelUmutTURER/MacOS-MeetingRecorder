#!/bin/bash
# Yayın çıkar: VERSION + CHANGELOG + etiket + GitHub release BİRLİKTE.
#
#   bin/_release.sh 1.0.1
#
# NEDEN SCRIPT: bu adımlardan biri atlanınca düzeltme yayında görünür ama
# kimseye ulaşmaz — `git pull` yapan alır, zip indiren almaz, güncelleme
# kontrolü ise yerel VERSION ile son yayını karşılaştırdığı için "güncelsin"
# der. Bir kez tam olarak bu oldu.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
export PATH=/opt/homebrew/bin:/usr/bin:/bin:$PATH
REPO=OncelUmutTURER/MacOS-MeetingRecorder
V="${1:-}"

[ -n "$V" ] || { echo "kullanım: bin/_release.sh <sürüm>   (örn: 1.0.1)"; exit 1; }
V="${V#v}"

grep -q "## \[$V\]" CHANGELOG.md || {
  echo "HATA: CHANGELOG.md içinde '## [$V]' başlığı yok."
  echo "      Önce değişiklikleri oraya yaz — yayın notu oradan üretiliyor."; exit 1; }
[ "$(tr -d ' \n' < VERSION)" = "$V" ] || {
  echo "HATA: VERSION dosyası '$(tr -d ' \n' < VERSION)', beklenen '$V'."; exit 1; }
[ -z "$(git status --porcelain)" ] || {
  echo "HATA: commit edilmemiş değişiklik var:"; git status --short; exit 1; }

echo "=== yayın notu (CHANGELOG'un $V bölümü) ==="
# DİKKAT: `-v` ile verilen değer awk'ta kaçış dizilerinden geçiyor, yani `\[`
# düz `[` oluyor ve REGEX olarak karakter sınıfı sayılıyor — başlık hiç eşleşmiyor.
# Bu yüzden regex değil, düz metin önek karşılaştırması (`index`) kullanılıyor.
NOTES=$(awk -v v="## [$V]" '
  index($0, v) == 1 {flag=1; next}
  /^## \[/ {flag=0}
  flag {print}' CHANGELOG.md)
echo "$NOTES" | head -12
[ -n "$(echo "$NOTES" | tr -d ' \n')" ] || { echo "HATA: bölüm boş."; exit 1; }

echo
echo "=== push ==="
git push origin main 2>&1 | tail -2

echo "=== etiket v$V ==="
git tag -a "v$V" -m "v$V" 2>/dev/null || git tag -f -a "v$V" -m "v$V"
git push -f origin "v$V" 2>&1 | tail -2

echo "=== GitHub release ==="
printf '%s\n' "$NOTES" > /tmp/relnotes.md
gh release create "v$V" -R "$REPO" --title "v$V" --notes-file /tmp/relnotes.md 2>&1 | tail -2 \
  || gh release edit "v$V" -R "$REPO" --notes-file /tmp/relnotes.md 2>&1 | tail -2
rm -f /tmp/relnotes.md

echo
echo "=== doğrulama ==="
./bin/_verifypublic.sh 2>&1 | sed -n '/sürüm \/ yayın/,/^$/p'
