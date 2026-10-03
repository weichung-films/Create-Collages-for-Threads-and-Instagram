#!/bin/bash
# Run Threads Collage on macOS without building the .app.
# Double click in Finder (first time: Control click > Open > Open).
cd "$(dirname "$0")" || exit 1
PY=""
for cand in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys, tkinter; assert sys.version_info >= (3, 9) and tkinter.TkVersion >= 8.6' >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Install Python 3.11+ from https://www.python.org/downloads/macos/ and try again."
  read -r -p "Press Return to close." _
  exit 1
fi
if [ ! -d .venv-mac ]; then
  "$PY" -m venv .venv-mac
  # shellcheck disable=SC1091
  source .venv-mac/bin/activate
  python -m pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv-mac/bin/activate
fi
python launcher.py "$@" &
disown
