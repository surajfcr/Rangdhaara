#!/usr/bin/env sh
# Start Rangdhaara on macOS or Linux: ./run.sh
set -e
cd "$(dirname "$0")"
VENV=".venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt
"$VENV/bin/python" manage.py setup
exec "$VENV/bin/python" manage.py serve
