"""Konuşma ortası kesilmelerin ARALIKLARINI ölçer — periyodik mi, rastgele mi.

Periyodik ve sabit uzunluklu boşluklar donanım/sürücü tampon sorununa işaret eder
(aggregate device drift düzeltmesi). Rastgele dağılım ise ağ/uygulama kaynaklıdır.
"""
import sys
import wave

import numpy as np

with wave.open(sys.argv[1], "rb") as w:
    sr, n = w.getframerate(), w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype=np.int16)

zero = (a == 0).view(np.int8)
d = np.diff(np.concatenate(([0], zero, [0])))
starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
keep = (ends - starts) >= int(sr * 0.02)
starts, ends = starts[keep], ends[keep]

edge = int(sr * 0.06)


def db(x):
    if x.size == 0:
        return -200.0
    r = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    return 20 * np.log10(r / 32768.0) if r > 0 else -200.0


ts, durs = [], []
for s, e in zip(starts, ends):
    if db(a[max(0, s - edge):s]) > -45 and db(a[e:e + edge]) > -45:
        ts.append(s / sr)
        durs.append((e - s) / sr * 1000)

ts, durs = np.array(ts), np.array(durs)
print(f"  kesilme sayısı     : {len(ts)}")
if len(ts) < 3:
    sys.exit()
print(f"  boşluk uzunluğu    : ortalama {durs.mean():.1f} ms · "
      f"std {durs.std():.1f} ms · min {durs.min():.0f} · max {durs.max():.0f}")
gaps = np.diff(ts)
gaps = gaps[gaps < 120]
print(f"  iki kesilme arası  : medyan {np.median(gaps):.2f} sn · "
      f"ortalama {gaps.mean():.2f} sn · std {gaps.std():.2f} sn")
# en sık aralık
hist, edges = np.histogram(gaps, bins=np.arange(0, 30, 0.5))
top = hist.argsort()[::-1][:4]
print("  en sık aralıklar   : " + " · ".join(
    f"{edges[i]:.1f}-{edges[i]+0.5:.1f} sn ({hist[i]}x)" for i in top))
print(f"  boşluk uzunluğu sabit mı: "
      f"{'EVET (std < 10 ms) -> tampon düşüşü' if durs.std() < 10 else 'hayır, değişken'}")
