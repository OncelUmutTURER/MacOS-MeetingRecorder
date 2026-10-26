#!/bin/bash
# Bu test Python'a taşındı: bin/_synctest.py  (sebep: varyantların ffmpeg
# argümanlarını ve ton konumu analizini kabukta tutmak okunaksızdı).
exec "$(cd "$(dirname "$0")/.." && pwd)/venv/bin/python" \
     "$(cd "$(dirname "$0")" && pwd)/_synctest.py" "$@"
