#!/usr/bin/env bash
# TNT GROUP - chay nhanh tren macOS/Linux
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Chua cai Python 3."; exit 1; }
command -v ffmpeg  >/dev/null || echo "Canh bao: chua thay ffmpeg trong PATH."
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt
python -m playwright install chromium
python app.py
