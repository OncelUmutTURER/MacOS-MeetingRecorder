"""Bir tarihe ait tüm toplantı klasörlerinin kanal seviyelerini ve sürelerini özetler.

Kullanım:  _survey.py <desen>     örn:  _survey.py 2026-09-24-*
"""
import glob
import os
import subprocess
import sys

FF = "/opt/homebrew/bin/ffmpeg"
FP = "/opt/homebrew/bin/ffprobe"


def db(p: str):
    r = subprocess.run([FF, "-hide_banner", "-i", p, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    for l in r.stderr.splitlines():
        if "mean_volume" in l:
            return float(l.split()[-2])
    return None


def dur(p: str) -> float:
    r = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


os.chdir(os.path.expanduser("~/Meetings"))
ds = sorted(d for d in glob.glob(sys.argv[1]) if os.path.isdir(d))
tot = 0.0
rows = []
for d in ds:
    s, m = f"{d}/sys.wav", f"{d}/mic.wav"
    if not os.path.exists(s):
        continue
    t = dur(s)
    tot += t
    rows.append((d, t, db(s), db(m) if os.path.exists(m) else None))

print(f"  klasör sayısı  : {len(rows)}")
print(f"  toplam ses     : {tot/60:.1f} dakika")
sv = [r[2] for r in rows if r[2] is not None]
mv = [r[3] for r in rows if r[3] is not None]
silent = sum(1 for x in sv if x < -60)
print(f"  sys SESSİZ (<-60 dB): {silent} / {len(sv)}")
if sv:
    print(f"  sys  min/ort/max : {min(sv):6.1f} / {sum(sv)/len(sv):6.1f} / {max(sv):6.1f} dB")
if mv:
    print(f"  mic  min/ort/max : {min(mv):6.1f} / {sum(mv)/len(mv):6.1f} / {max(mv):6.1f} dB")
print()
print("  ilk 4 ve son 4:")
for d, t, s, m in rows[:4] + rows[-4:]:
    print(f"    {d:<26} {t:5.0f} sn   sys {s if s is None else f'{s:6.1f}'}   "
          f"mic {m if m is None else f'{m:6.1f}'}")
