#!/bin/bash
# 30 saniyelik doğrulama kaydı: mikrofon kanalı = sen, loopback kanalı = Türkçe TTS
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
clear
echo "════════════════════════════════════════════════════════════"
echo "  KAYIT KATMANI TESTİ"
echo "  macOS mikrofon izni isterse İZİN VER'e bas."
echo "════════════════════════════════════════════════════════════"
echo
meet start "test" || { echo; echo "Kayıt başlatılamadı."; exit 1; }
sleep 2

echo
echo "────────────────────────────────────────────────────────────"
echo "  ŞİMDİ MİKROFONA KONUŞ — 12 saniyen var."
echo "  Öneri: \"Tamam, cuma gününe kadar hazırlarım."
echo "          I will set up a meeting with the supplier as well.\""
echo "────────────────────────────────────────────────────────────"
for i in $(seq 12 -1 1); do printf "\r  konuş... %2d " "$i"; sleep 1; done
printf "\r  bitti.        \n"

echo
echo "  Şimdi hoparlörden/kulaklıktan Türkçe konuşma çalıyor (loopback testi)..."
say -v Samantha -r 170 "Hello, this is a test recording. We will talk about refractory life at the plant. According to the laser scan the hot spot area needs attention. The tundish situation will be reviewed separately. Can you talk to the crew and have the report ready by Friday?"

sleep 2
echo
meet stop
echo
echo "════════════════════════════════════════════════════════════"
echo "  Test bitti. Yukarıdaki mic.wav / sys.wav seviyelerine bak."
echo "════════════════════════════════════════════════════════════"
