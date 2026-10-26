#!/bin/bash
# Kurulumun sağlıklı olduğunu baştan sona doğrular. Taşımadan sonra ya da bir şey
# tuhaf davrandığında çalıştır.
export PATH="/opt/homebrew/bin:$PATH"
SELF="$0"; while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
PY="$ROOT/venv/bin/python"

echo "kök: $ROOT"
echo

"$PY" - <<'PY'
import sys, os, shutil, subprocess
from pathlib import Path
ROOT = Path(os.environ["PYTHONPATH"]).parent
sys.path.insert(0, str(ROOT / "src"))
ok = True
def check(label, fn):
    global ok
    try:
        msg = fn()
        print(f"  ✓ {label:<34} {msg}")
    except Exception as exc:
        ok = False
        print(f"  ✗ {label:<34} {exc}")

def _root():
    from common import ROOT as R
    assert R.resolve() == ROOT.resolve(), f"{R} != {ROOT}"
    return str(R)

def _deps():
    import torch, mlx.core, silero_vad, soundfile          # noqa: F401
    from pyannote.audio import Pipeline                     # noqa: F401
    import mlx_whisper                                      # noqa: F401
    return "torch · mlx · silero-vad · pyannote · mlx-whisper"

def _modules():
    import record, transcribe, diarize, pipeline, menubar, audiodev, notify, agent, miccheck, audiowatch  # noqa
    return "10 modül"

def _coreaudio():
    import audiowatch
    audiowatch.selftest()
    live = [n for n, i, r in audiowatch.snapshot() if r]
    return f"IsRunningSomewhere okunuyor, şu an aktif: {live or 'yok'}"

def _tools():
    missing = [t for t in ("ffmpeg", "ffprobe", "SwitchAudioSource", "terminal-notifier")
               if not shutil.which(t)]
    assert not missing, f"eksik: {missing}"
    return "ffmpeg · ffprobe · SwitchAudioSource · terminal-notifier"

def _blackhole():
    out = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation",
                          "-list_devices", "true", "-i", ""],
                         capture_output=True, text=True).stderr
    assert "BlackHole 2ch" in out, "BlackHole 2ch bulunamadı"
    return "kurulu"

def _outputs():
    import audiodev
    from common import load_config
    cfg = load_config()
    outs = audiodev.list_outputs()
    want = [p["output"] for p in cfg["audio"]["output_profiles"]]
    missing = [w for w in want if w not in outs]
    assert not missing, f"Audio MIDI Setup'ta eksik: {missing}"
    target, why = audiodev.pick_output_profile(cfg)
    return f"{' · '.join(want)}  → şu an: {target} ({why})"

def _mic():
    import record
    from common import load_config
    name, idx = record._pick_mic(load_config())
    return f"[{idx}] {name}"

def _env():
    from common import ENV_PATH, load_env
    assert ENV_PATH.exists(), f"{ENV_PATH} yok"
    mode = oct(ENV_PATH.stat().st_mode)[-3:]
    assert mode == "600", f"izinler {mode}, 600 olmalı"
    env = load_env()
    assert env.get("HF_TOKEN"), "HF_TOKEN yok"
    return "HF_TOKEN var, mod 600"

def _glossary():
    # Sözlük İSTEĞE BAĞLI: yoksa terim düzeltme adımı sessizce atlanır. Herkese
    # açık depoda gerçek sözlük yok (kuruma özel), örnek şablon var.
    g = ROOT / "glossary.md"
    if not g.exists():
        ex = ROOT / "glossary.example.md"
        return ("yok — terim düzeltme kapalı"
                + (f" (başlamak için: cp {ex.name} {g.name})" if ex.exists() else ""))
    return f"{len(g.read_text().splitlines())} satır"

def _meetings():
    from common import load_config, meetings_dir
    d = meetings_dir(load_config())
    assert d.exists(), f"{d} yok"
    n = len([p for p in d.iterdir() if p.is_dir()])
    return f"{d}  ({n} toplantı)"

def _agent():
    import subprocess
    label = "com.meeting-pipeline.menubar"
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "launchd agent kayıtlı değil (meet install-agent)"
    assert str(ROOT) in r.stdout, "agent ESKİ yolu gösteriyor — meet install-agent çalıştır"
    running = "state = running" in r.stdout
    return "kayıtlı, " + ("çalışıyor" if running else "ÇALIŞMIYOR")

def _ghosts():
    """Bitmiş toplantılara yazmayı sürdüren ffmpeg süreçleri.

    Bunlar mikrofonu açık tutar; birden fazlası aynı CoreAudio girişi için
    yarıştığında yeni açılan akış sıfır örnek alır ve kayıt sessiz çıkar.
    Bir kez 9 güne kadar yaşayan 10 süreç birikmişti.
    """
    from record import orphan_ffmpeg
    from common import load_config, meetings_dir, read_state
    st = read_state()
    active = Path(st["session_dir"]) if st else None
    g = orphan_ffmpeg(meetings_dir(load_config()), active)
    assert not g, ("sahipsiz ffmpeg süreci: "
                   + ", ".join(f"pid {o['pid']} ({o['etime']})" for o in g)
                   + " — temizlemek için: meet audit --fix")
    return "yok" + (" (aktif kayıt sürüyor, o hariç)" if active else "")

def _symlink():
    link = Path.home() / ".local" / "bin" / "meet"
    assert link.exists(), f"{link} yok"
    tgt = Path(os.readlink(link))
    assert tgt.resolve() == (ROOT / "bin" / "meet").resolve(), f"yanlış hedef: {tgt}"
    return str(tgt)

check("proje kökü",            _root)
check("PATH araçları",         _tools)
check("BlackHole sürücüsü",    _blackhole)
check("ağır bağımlılıklar",    _deps)
check("proje modülleri",       _modules)
check("CoreAudio bayrağı",     _coreaudio)
check("Multi-Output cihazlar", _outputs)
check("mikrofon seçimi",       _mic)
check(".env",                  _env)
check("glossary.md",           _glossary)
check("Meetings klasörü",      _meetings)
check("hayalet ffmpeg",        _ghosts)
check("~/.local/bin/meet",     _symlink)
check("launchd agent",         _agent)

print()
print("SONUÇ: " + ("hepsi geçti" if ok else "BAZI KONTROLLER BAŞARISIZ (yukarı bak)"))
sys.exit(0 if ok else 1)
PY
