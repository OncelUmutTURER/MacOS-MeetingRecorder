"""Bir kayıtta GERÇEK ses kesilmesi (dropout) olup olmadığını ölçer.

Doğal duraklama ile dropout'u ayırt etmek gerekiyor: ikisi de "sessizlik" görünür.
Ayırt edici işaret, sessizliğin KONUŞMANIN ORTASINDA başlamasıdır. Bu script
sessizlik bloklarının hemen öncesindeki ve sonrasındaki 60 ms'ye bakar; iki taraf
da yüksek sesliyse bu bir duraklama değil, akıştan düşen örneklerdir.

Kullanım:  _dropouts.py <wav> [<wav> ...]
"""
import sys
import wave

import numpy as np

ZERO_RUN_MS = 20      # bu kadar sürekli tam-sıfır örnek = dijital boşluk
EDGE_MS = 60          # boşluğun iki yanında bakılacak pencere
LOUD_DB = -45         # bu seviyenin üstü "konuşma sürüyordu" demek


def db(x: np.ndarray) -> float:
    if x.size == 0:
        return -200.0
    r = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    return 20 * np.log10(r / 32768.0) if r > 0 else -200.0


def analyse(path: str) -> None:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16)
    dur = len(a) / sr

    zero = a == 0
    # tam-sıfır koşularını bul
    d = np.diff(np.concatenate(([0], zero.view(np.int8), [0])))
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    min_run = int(sr * ZERO_RUN_MS / 1000)
    lens = ends - starts
    keep = lens >= min_run
    starts, ends, lens = starts[keep], ends[keep], lens[keep]

    edge = int(sr * EDGE_MS / 1000)
    mid_speech = 0
    examples = []
    for s, e in zip(starts, ends):
        before = a[max(0, s - edge):s]
        after = a[e:e + edge]
        if db(before) > LOUD_DB and db(after) > LOUD_DB:
            mid_speech += 1
            if len(examples) < 8:
                examples.append((s / sr, (e - s) / sr * 1000,
                                 db(before), db(after)))

    zero_total = float(lens.sum()) / sr
    print(f"\n{path}")
    print(f"  süre                      : {dur/60:.1f} dk ({dur:.0f} sn)")
    print(f"  dijital boşluk (>{ZERO_RUN_MS} ms) : {len(starts)} adet, "
          f"toplam {zero_total:.1f} sn ({zero_total/dur*100:.1f}%)")
    print(f"  KONUŞMA ORTASINDA kesilme : {mid_speech} adet   <-- asıl sayı")
    if len(starts):
        print(f"  boşluk süresi medyan/max  : {np.median(lens)/sr*1000:.0f} ms / "
              f"{lens.max()/sr*1000:.0f} ms")
    if examples:
        print("  örnekler (konum · süre · öncesi · sonrası):")
        for t, ms, b, aa in examples:
            print(f"    {int(t//60):02d}:{t%60:05.2f}  {ms:6.0f} ms  "
                  f"{b:6.1f} dB -> {aa:6.1f} dB")
    if mid_speech == 0:
        print("  ✓ konuşma ortasında kesilme yok — sessizlikler doğal duraklama")
    elif mid_speech < 5:
        print("  ~ birkaç kesilme var, ihmal edilebilir sayılabilir")
    else:
        print(f"  ✗ dakikada {mid_speech/(dur/60):.1f} kesilme — akış düşüyor")


for p in sys.argv[1:]:
    analyse(p)
