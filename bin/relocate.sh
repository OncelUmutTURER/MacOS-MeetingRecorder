#!/bin/bash
# Projeyi başka bir klasöre taşır ve taşımada kırılan bağları onarır.
#
#   ./bin/relocate.sh /yeni/tam/yol/meeting-pipeline
#
# Neden gerekli: kod kendi kökünü __file__ / $0 üzerinden bulduğu için taşınabilir,
# ama proje DIŞINDA yolu bilen dört şey var ve taşıyınca sessizce kırılırlar:
#   1. venv/bin/* içindeki gömülü mutlak yollar (pip, torchrun, activate, ...)
#   2. ~/.local/bin/meet symlink'i
#   3. launchd agent plist'i
#   4. Cowork'teki meeting-actions skill'i  <- BUNU SCRIPT YAPAMAZ, elle yapılacak
set -euo pipefail

SELF="$0"
while [ -L "$SELF" ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in /*) SELF="$LINK" ;; *) SELF="$(dirname "$SELF")/$LINK" ;; esac
done
OLD="$(cd "$(dirname "$SELF")/.." && pwd)"

die() { printf '\n\033[31mHATA:\033[0m %s\n' "$1" >&2; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }

[ $# -eq 1 ] || die "kullanım: $0 /yeni/tam/yol/meeting-pipeline"
NEW="$1"
case "$NEW" in /*) ;; *) die "hedef mutlak yol olmalı: $NEW" ;; esac
[ -e "$NEW" ] && die "hedef zaten var: $NEW"
[ "$OLD" = "$NEW" ] && die "kaynak ve hedef aynı"
mkdir -p "$(dirname "$NEW")" || die "hedefin üst klasörü oluşturulamadı"

echo "Kaynak: $OLD"
echo "Hedef : $NEW"

# --- kayıt sürüyorsa dur --------------------------------------------------
if [ -f "$OLD/.state.json" ]; then
  die "Kayıt sürüyor gibi görünüyor ($OLD/.state.json). Önce kaydı durdur."
fi

step "1/6  Çalışan süreçler durduruluyor"
"$OLD/bin/meet" uninstall-agent >/dev/null 2>&1 || true
pkill -f 'src/menubar.py'      2>/dev/null || true
pkill -f 'src/record.py guard' 2>/dev/null || true
sleep 1
ok "menü çubuğu ve guard durduruldu"

step "2/6  Klasör taşınıyor"
mv "$OLD" "$NEW" || die "taşıma başarısız"
ok "$(du -sh "$NEW" | cut -f1) taşındı"

step "3/6  venv içindeki gömülü mutlak yollar yeniden yazılıyor"
COUNT=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  LC_ALL=C sed -i '' "s|$OLD|$NEW|g" "$f" && COUNT=$((COUNT+1))
done < <(grep -rl "$OLD" "$NEW/venv/bin" 2>/dev/null || true)
[ -f "$NEW/pyvenv.cfg" ] && LC_ALL=C sed -i '' "s|$OLD|$NEW|g" "$NEW/pyvenv.cfg" 2>/dev/null || true
ok "$COUNT dosya güncellendi"
if grep -rl "$OLD" "$NEW/venv/bin" >/dev/null 2>&1; then
  echo "  ⚠ hâlâ eski yolu içeren dosyalar var:"
  grep -rl "$OLD" "$NEW/venv/bin" | head -5 | sed 's/^/      /'
fi

step "4/6  PATH symlink'i tazeleniyor"
mkdir -p "$HOME/.local/bin"
ln -sf "$NEW/bin/meet" "$HOME/.local/bin/meet"
ok "$(readlink "$HOME/.local/bin/meet")"

step "5/6  launchd agent yeni yolla kuruluyor"
"$NEW/bin/meet" install-agent >/dev/null || die "agent kurulamadı"
sleep 3
pgrep -f 'src/menubar.py' >/dev/null && ok "menü çubuğu çalışıyor" \
  || echo "  ⚠ menü çubuğu başlamadı — log: ~/Library/Logs/meeting-pipeline-menubar.log"

step "6/6  Doğrulama"
# Yüzeysel import yetmez; ağır bağımlılıklar gerçekten yüklenmeli.
PYTHONPATH="$NEW/src" "$NEW/venv/bin/python" - <<PY || die "ağır bağımlılıklar yüklenemedi"
import sys; sys.path.insert(0, "$NEW/src")
import torch, mlx.core, silero_vad, soundfile          # noqa: F401
from pyannote.audio import Pipeline                     # noqa: F401
import record, transcribe, diarize, pipeline, menubar, audiodev, notify, agent, miccheck
from common import ROOT
import pathlib
want = pathlib.Path("$NEW").resolve()
# /tmp -> /private/tmp gibi symlink'li yollarda ham karşılaştırma tutmaz
assert ROOT.resolve() == want, f"ROOT yanlış: {ROOT.resolve()} != {want}"
print("  \033[32m✓\033[0m torch / mlx / silero-vad / pyannote ve tüm modüller yüklendi")
print(f"  \033[32m✓\033[0m ROOT = {ROOT}")
PY

"$HOME/.local/bin/meet" devices >/dev/null 2>&1 && ok "symlink üzerinden 'meet devices' çalışıyor" \
  || die "symlink üzerinden çalışmıyor"

cat <<EOF

────────────────────────────────────────────────────────────
TAŞIMA TAMAM.

Kalan TEK elle iş — script bunu yapamaz:

  Cowork'te 'meeting-actions' skill'i glossary.md'nin TAM YOLUNU içeriyor.
  Skill dosyada değil, kullanıcı hesabında saklanıyor.

  Cowork'e şunu söyle:
    "meeting-actions skill'indeki glossary yolunu şununla güncelle:
     $NEW/glossary.md"

Son doğrulama (ağır, ~1 dk) — mevcut bir toplantıyı yeniden işle:

  meet transcribe ~/Meetings/<bir-toplanti-klasoru>

Toplantı kayıtları taşınmadı, config.yaml'daki paths.meetings_dir'da duruyor.
Model önbelleği (~3 GB) ~/.cache/huggingface altında, taşınmaz.
────────────────────────────────────────────────────────────
EOF
