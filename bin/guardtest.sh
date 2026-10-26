#!/bin/bash
# Uçtan uca guard testi.
# Senaryo: kulaklıksız başla, ortada kulaklığı tak, iki dilli iki karşı taraf konuşsun.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

hr() { printf '\n%s\n' "────────────────────────────────────────────────────────────"; }
countdown() { for i in $(seq "$1" -1 1); do printf "\r  %2d " "$i"; sleep 1; done; printf "\r      \n"; }

clear
cat <<'INTRO'
════════════════════════════════════════════════════════════
  UÇTAN UCA GUARD TESTİ  (~3 dakika)

  Okuma metni ayrı pencerede açık olmalı: TEST-OKUMA-METNI.md

  ÖNEMLİ: şu anda kulaklığın BAĞLI OLMAMALI.
  Bağlıysa Bluetooth'tan bağlantısını kes ve 15 saniye bekle.
════════════════════════════════════════════════════════════
INTRO
read -r -p "  Kulaklık bağlı değil, hazırım (Enter)..."

hr
meet start "guard-test" || exit 1

hr
echo "  ▶ BÖLÜM 1'i oku. 55 saniyen var."
echo "    (metindeki ilk blok — tesis durumu)"
countdown 55

hr
echo "  ▶ Şimdi karşı taraf konuşuyor (Türkçe)..."
say -v Samantha -r 175 "I was thinking the same thing. The wear started early in the last cycle as well, so refreshing the scan makes sense. Shall I let the crew know, or will you?"
sleep 1

hr
echo "  ▶▶▶ ŞİMDİ KULAKLIĞINI TAK. <<<"
echo "      Bluetooth bağlanana kadar bekle, sonra 25 saniye daha bekleyeceğiz."
echo "      Bu sırada başka bir pencerede guard.log'u izleyebilirsin."
countdown 35

hr
echo "  ▶ BÖLÜM 2'yi oku. 55 saniyen var."
echo "    (metindeki ikinci blok — kararlar ve action item'lar)"
countdown 55

hr
echo "  ▶ Karşı taraf konuşuyor (İngilizce)..."
say -v Daniel -r 180 "Thanks. I agree on doing it early. Please send me the scan report by Friday and I will get the crew scheduled. One question, does that measurement include the outer layer, or only the inner one?"
sleep 1

echo "  ▶ Kapanış (Türkçe)..."
say -v Samantha -r 175 "All right, let us reopen that topic after I have spoken to the supplier. I will remind the team about the dashboard delay."
sleep 2

hr
meet stop

hr
echo "  Test bitti. Transkript arka planda çalışıyor."
echo "  İzlemek için:  tail -f ~/Meetings/*guard-test/process.log"
echo
echo "  Guard logu:"
tail -20 ~/Meetings/*guard-test/guard.log 2>/dev/null || echo "    (guard.log okunamadı)"
