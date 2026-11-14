#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v apt-get >/dev/null; then
  echo 'This installer supports Debian/Ubuntu (apt-get). Install ffmpeg, Python venv/pip, and DejaVu fonts manually on other distributions.' >&2
  exit 1
fi
SUDO=()
if (( EUID != 0 )); then
  if ! command -v sudo >/dev/null; then echo 'sudo is required for system packages.' >&2; exit 1; fi
  SUDO=(sudo)
fi
missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
command -v ffprobe >/dev/null || missing+=(ffmpeg)
command -v python3 >/dev/null || missing+=(python3)
dpkg-query -W -f='${Status}' python3-venv 2>/dev/null | grep -q 'install ok installed' || missing+=(python3-venv)
dpkg-query -W -f='${Status}' python3-pip 2>/dev/null | grep -q 'install ok installed' || missing+=(python3-pip)
dpkg-query -W -f='${Status}' libgl1 2>/dev/null | grep -q 'install ok installed' || missing+=(libgl1)
dpkg-query -W -f='${Status}' libglib2.0-0 2>/dev/null | grep -q 'install ok installed' || missing+=(libglib2.0-0)
if [[ ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf && ! -f /usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf ]]; then
  missing+=(fonts-dejavu-core)
fi
if ((${#missing[@]})); then
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y "${missing[@]}"
fi
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip setuptools wheel
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python main.py --bootstrap
./venv/bin/python main.py --download-demo
echo 'Ready. Run ./venv/bin/python main.py --test-demo or ./venv/bin/python app.py'
