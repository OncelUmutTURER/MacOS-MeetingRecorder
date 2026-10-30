#!/bin/bash
# GitHub'daki HERKESE AÇIK hâli doğrular: taze klon + TÜM GEÇMİŞ taraması.
#
# `bin/_publishcheck.sh` push ÖNCESİ sahnelenen değişikliğe bakar; bu ise push
# SONRASI dünyanın gördüğüne bakar ve eski commit'lerde kalmış bir şey var mı
# diye bütün geçmişi tarar. İkisi farklı sorular: bir sır sahneden çıkarılmış
# ama geçmişte duruyor olabilir.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:$PATH
REPO_URL="${1:-https://github.com/OncelUmutTURER/MacOS-MeetingRecorder.git}"
PAT_FILE="$(cd "$(dirname "$0")/.." && pwd)/.publishcheck-patterns"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git clone -q "$REPO_URL" "$TMP/repo" || { echo "klonlanamadı"; exit 1; }
cd "$TMP/repo"

echo "=== geçmiş ==="
git log --oneline --all | cat
echo "   commit sayısı: $(git rev-list --all --count)"

echo
echo "=== TÜM GEÇMİŞTE kuruma özel ad taraması ==="
FAIL=0
REVS=$(git rev-list --all)
# Tarayıcıların KENDİSİ aradıkları kalıpları içerir (desen listesi, örnek anahtar
# biçimleri, "şuna dikkat" yorumları). Onları hariç tutmazsak her çalıştırmada
# kendi kuyruğunu yakalar — ilk sürümde tam olarak bu oldu.
EXCL=(":(exclude)bin/_publishcheck.sh" ":(exclude)bin/_verifypublic.sh")
scan() {
  local p="$1"
  local n
  n=$(git grep -ri -l -- "$p" $REVS -- . "${EXCL[@]}" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" != "0" ]; then
    echo "  ✗ $p — $n yerde"
    git grep -ri -l -- "$p" $REVS -- . "${EXCL[@]}" 2>/dev/null | head -3 | sed 's/^/        /'
    FAIL=1
  fi
}
if [ -f "$PAT_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    scan "$line"
  done < "$PAT_FILE"
  echo "  ($(grep -cvE '^\s*(#|$)' "$PAT_FILE") desen tarandı)"
else
  echo "  ⚠ $PAT_FILE yok — kuruma özel adlar taranamadı"
fi

echo
echo "=== anahtar/token kalıpları (tüm geçmiş) ==="
for p in 'hf_[A-Za-z0-9]{12}' 'sk-ant-[A-Za-z0-9_-]{12}' 'ghp_[A-Za-z0-9]{12}' \
         'AKIA[A-Z0-9]{12}' 'BEGIN.?[A-Z]*.?PRIVATE.?KEY'; do
  if git grep -rIE -l -- "$p" $REVS -- . "${EXCL[@]}" >/dev/null 2>&1; then
    echo "  ✗ $p bulundu"
    git grep -rIE -l -- "$p" $REVS -- . "${EXCL[@]}" 2>/dev/null | head -3 | sed 's/^/        /'
    FAIL=1
  fi
done
[ "$FAIL" = "0" ] && echo "  ✓ temiz"

echo
echo "=== gizli dosyalar hiç commit edilmiş mi ==="
for f in .env config.yaml glossary.md glossary-candidates.md CLAUDE.md \
         TEST-OKUMA-METNI.md .publishcheck-patterns .update-check.json; do
  if [ -n "$(git log --all --oneline -- "$f" 2>/dev/null | head -1)" ]; then
    echo "  ✗ $f GEÇMİŞTE VAR"; FAIL=1
  else
    echo "  ✓ $f hiç girmemiş"
  fi
done

echo
echo "=== sürüm / yayın tutarlılığı ==="
# NEDEN VAR: 2026-10-30'da kullanıcı sordu — "yeni commit'i eklemişsin ama yeni
# release çıkmamışsın, latest release'i alan biri bu değişiklikleri alabilecek mi?"
# Hayır: `git pull` yapan alır, yayının zip'ini indiren almaz, VE güncelleme
# kontrolü yerel VERSION ile son YAYINI karşılaştırdığı için kimseye haber bile
# gitmez. Düzeltme yayında olup kimsenin haberi olmaması en kötü hâl.
SLUG=$(echo "$REPO_URL" | sed -E 's#.*github\.com[:/]##; s#\.git$##')
VER=$(tr -d ' \n' < VERSION 2>/dev/null)
REL=$(gh release view --repo "$SLUG" --json tagName -q .tagName 2>/dev/null)
echo "  VERSION dosyası : ${VER:-yok}"
echo "  son yayın       : ${REL:-yok}"
if [ -z "$REL" ]; then
  echo "  ⚠ hiç yayın yok — güncelleme kontrolü sessiz kalır"
else
  AFTER=$(git rev-list "$REL"..origin/HEAD --count 2>/dev/null || echo "?")
  echo "  yayından sonraki commit: $AFTER"
  if [ "${VER#v}" != "${REL#v}" ]; then
    echo "  ✗ VERSION ile yayın etiketi UYUŞMUYOR — sürüm numarası neyin"
    echo "    çalıştığını söylemiyor"; FAIL=1
  fi
  if [ "$AFTER" != "0" ] && [ "$AFTER" != "?" ]; then
    echo "  ✗ main'de yayınlanmamış $AFTER commit var — kullanıcılara haber GİTMEZ"
    echo "    (VERSION'ı yükselt, CHANGELOG'a yaz, etiketle, gh release create)"
    FAIL=1
  fi
  [ "$AFTER" = "0" ] && [ "${VER#v}" = "${REL#v}" ] && echo "  ✓ yayın main ile aynı"
fi

echo
echo "=== klonun kullanılabilirliği ==="
for f in README.md CHANGELOG.md LICENSE VERSION config.example.yaml \
         glossary.example.md docs/SETUP.md docs/GLOSSARY.md docs/ACTION-ITEMS.md \
         ENGINEERING-NOTES.md bin/meet src/record.py; do
  [ -f "$f" ] || { echo "  ✗ $f eksik"; FAIL=1; }
done
echo "  sürüm: $(cat VERSION 2>/dev/null)"
[ "$FAIL" = "0" ] && echo "  ✓ gerekli dosyaların hepsi var"

echo
if [ "$FAIL" = "0" ]; then echo "SONUÇ: yayınlanan hâl temiz"; else echo "SONUÇ: SORUN VAR"; fi
exit "$FAIL"
