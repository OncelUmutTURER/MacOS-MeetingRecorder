"""_is_prompt_echo regresyon testi.

Jargon listesini (config.yaml -> transcribe.jargon_sets) her değiştirdiğinde çalıştır:

    meet test

Gerçek sızıntılar yakalanmalı; terimlerin doğal geçtiği cümleler KESİNLİKLE
yakalanmamalı. İkinci grup daha önemli — yanlış pozitif gerçek konuşmayı siler.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from transcribe import _is_prompt_echo   # noqa: E402

# Test kendi jargon listesini kullanıyor; kullanıcının config.yaml'ındaki terimlere
# BAĞLI DEĞİL. Temiz bir klonda o liste bambaşka olur ve test hiçbir şey sınamadan
# geçerdi. Aşağıdaki sızıntı örnekleri gerçek bir 97 dakikalık kayıttan alındı,
# yalnızca ürün adları jenerikle değiştirildi.
JARGON = ["Acme Core", "Widget-X", "Insite", "Copilot", "LLM", "ladle", "refractory",
          "tundish", "slagline", "hot spot", "pulpit", "laser scan"]

# Whisper bu satırları, konuşulmadığı halde prompt'tan kopyalayıp transkripte yazdı.
SHOULD_DROP = [
    "Acme Core, Widget-X, Insite, Copilot, LLM, ladle, refractory, tundish, "
    "slagline, hot spot, pulpit, laser scan.",
    "Acme Core, Widget-X, Insite, Copilot, ladle, refractory, tundish, slagline, "
    "hot spot, pulpit, laser scan.",
    "Acme Core. Acme Core, Widget-X, Insite, Copilot, Northwind, "
    "ladle, refractory.",
    "ladle refractory tundish slagline hot spot pulpit laser scan",
]

# Aynı terimlerin DOĞAL geçtiği gerçek cümleler. Bu grup birinciden daha kritik:
# yanlış pozitif gerçek konuşmayı siler.
SHOULD_KEEP = [
    "Riverside tesisinde üç numaralı ladle'ın refractory ömrü beklediğimizden hızlı düşüyor.",
    "Son laser scan'de slagline bölgesinde ciddi aşınma göründü, freeboard ölçümü de sınırda.",
    "Widget-X'in hot spot verileriyle karşılaştırdığımızda ikisi örtüşüyor, sensör hatası değil.",
    "Northwind tarafında tundish durumu ayrıca değerlendirilecek, Ahmet Bey'le konuşalım.",
    "Insite'taki dashboard heat verilerini iki saat gecikmeyle gösteriyor.",
    "Acme Core üzerinden mi ilerleyeceğiz yoksa mevcut kurulumu mu genişleteceğiz?",
    # en zor durum: yedi terim ama gerçek cümle
    "Yani ladle, refractory ve slagline tarafında pulpit ekibiyle laser scan planı yapacağız.",
    "Tamam.",
    "",
]


def main() -> int:
    jargon = JARGON
    print("jargon listesi:", ", ".join(jargon), f"({len(jargon)} terim)\n")
    fails = 0

    print("── atılması gerekenler (prompt sızıntısı) ──")
    for t in SHOULD_DROP:
        got = _is_prompt_echo(t, jargon)
        fails += 0 if got else 1
        print(f"  {'✓' if got else '✗ YAKALAYAMADI'}  {t[:74]}")

    print("\n── korunması gerekenler (gerçek konuşma) ──")
    for t in SHOULD_KEEP:
        got = _is_prompt_echo(t, jargon)
        fails += 1 if got else 0
        print(f"  {'✗ YANLIŞ POZİTİF' if got else '✓'}  {t[:74] or '(boş)'}")

    print(f"\nSONUÇ: {'hepsi geçti' if not fails else f'{fails} BAŞARISIZ'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
