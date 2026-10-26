#!/bin/bash
# Ses altyapısının BOŞTAYKEN ne kadar CPU yaktığını ölçer.
#
#   meet cpu            20 saniyelik ölçüm
#   meet cpu 60         60 saniyelik ölçüm
#
# Neden ps/top değil de CPU zamanı farkı: `ps -o pcpu` süreç ömrü boyunca çürüyen
# bir ortalama, `top` ise tahmin veriyor — ikisi de aynı anda çok farklı sayılar
# gösterdi. İki zaman damgası arasındaki gerçek CPU saniyesi farkı tartışmasız.
#
# Beklenen: hiçbir ses çalmazken ve kayıt yokken coreaudiod ~%0-1 olmalı.
# Sürekli %10 üzeriyse coreaudiod bozuk durumda demektir; çözümü yeniden başlatmak:
#     sudo killall coreaudiod
# (ses ~2 saniye kesilir, kendiliğinden geri gelir; çalan uygulamayı yeniden başlat.)
export PATH="/opt/homebrew/bin:$PATH"
SECS="${1:-20}"

pid_of() { pgrep -x "$1" | head -1; }
CA=$(pid_of coreaudiod)
MR=$(pid_of mediaremoted)

echo "Ses altyapısı CPU ölçümü — $SECS saniye"
echo
if command -v SwitchAudioSource >/dev/null 2>&1; then
  echo "  çıkış cihazı : $(SwitchAudioSource -c -t output)"
  echo "  giriş cihazı : $(SwitchAudioSource -c -t input)"
fi
NFF=$(pgrep -x ffmpeg | wc -l | tr -d ' ')
echo "  çalışan ffmpeg: $NFF"
[ "$NFF" != "0" ] && echo "  ⚠ kayıt sürüyor ya da hayalet süreç var — ölçüm boşta değil (meet audit)"
echo

snap() { ps -p "$1" -o time= 2>/dev/null | tr -d ' '; }
A_CA=$(snap "$CA"); A_MR=$(snap "$MR")
A_BH=$(ps -Ao pid=,comm= | grep -i blackhole | awk '{print $1}' | head -1)
A_BHT=$(snap "$A_BH")
T0=$(date +%s)
sleep "$SECS"
B_CA=$(snap "$CA"); B_MR=$(snap "$MR"); B_BHT=$(snap "$A_BH")
T1=$(date +%s)

/usr/bin/python3 - "$((T1-T0))" "$A_CA" "$B_CA" "$A_MR" "$B_MR" "$A_BHT" "$B_BHT" <<'PY'
import sys
def sec(s):
    s = (s or "0:00.00").strip()
    base = 0
    if '-' in s:
        d, s = s.split('-'); base = int(d) * 86400
    p = [float(x) for x in s.split(':')]
    while len(p) < 3: p.insert(0, 0.0)
    return base + p[0]*3600 + p[1]*60 + p[2]
el = float(sys.argv[1]); a = sys.argv[2:]
rows = [("coreaudiod", 0), ("mediaremoted", 1), ("BlackHole sürücüsü", 2)]
worst = 0.0
for name, i in rows:
    try:
        pct = (sec(a[i*2+1]) - sec(a[i*2])) / el * 100
    except Exception:
        continue
    if name == "coreaudiod": worst = pct
    print(f"  {name:<22} %{pct:5.1f}")
print()
if worst < 2:
    print("  ✓ normal")
elif worst < 8:
    print("  ~ yüksek sayılır ama kritik değil")
else:
    print("  ✗ coreaudiod boştayken bu kadar yakmamalı.")
    print("    Çözüm:  sudo killall coreaudiod    (ses 2 sn kesilir, geri gelir)")
    print("    Sonra bu komutu tekrar çalıştırıp karşılaştır.")
PY
