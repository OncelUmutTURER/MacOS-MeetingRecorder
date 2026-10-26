#!/bin/bash
# kAudioDevicePropertyDeviceIsRunningSomewhere bayrağı gerçekten kalkıyor mu?
# Otomatik kayıt tespiti bu bayrağa dayanıyor; çalışmıyorsa tasarım değişmeli.
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
W="$ROOT/venv/bin/python $ROOT/src/audiowatch.py --list"
export PYTHONPATH="$ROOT/src"

show() { printf '  %-34s %s\n' "$1" "$($W 2>/dev/null | grep -E "$2" | tr -s ' ' | sed 's/^ //')"; }

echo "════ 1. Hiçbir şey çalmıyorken (referans) ════"
$W
echo
echo "════ 2. ÇIKIŞ testi — afplay ile ses çalarken ════"
echo "   (çıkış cihazları TCC izni gerektirmez; bayrak okuma yöntemi doğru mu, bunu söyler)"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=440:duration=8" \
       -af "volume=-30dB" -ar 48000 -ac 2 /tmp/flagtone.wav
afplay /tmp/flagtone.wav &
APID=$!
sleep 2
$W
kill $APID 2>/dev/null; wait $APID 2>/dev/null
echo
echo "════ 3. GİRİŞ testi — ffmpeg BlackHole'u kaydederken ════"
BH=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
     | grep -iE '\] \[[0-9]+\] BlackHole 2ch$' | sed -E 's/.*\[([0-9]+)\].*/\1/' | head -1)
ffmpeg -hide_banner -loglevel error -y -f avfoundation -i ":$BH" -t 8 \
       -ar 16000 -ac 1 /tmp/flagcap.wav >/dev/null 2>&1 &
FPID=$!
sleep 3
$W
kill -INT $FPID 2>/dev/null; wait $FPID 2>/dev/null
echo
echo "Beklenen: 2. adımda çalınan çıkış cihazı ●, 3. adımda BlackHole IN ● olmalı."
echo "İkisi de ○ kalırsa bayrak okuma yöntemi yanlış."
echo "Sadece 3. adım ○ kalırsa giriş cihazlarında TCC/izin farkı var."
