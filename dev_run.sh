#!/usr/bin/env bash
set -euo pipefail
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Copy .env.sample to .env if .env doesn't exist
if [ ! -f .env ]; then
  cp .env.sample .env || true
fi
python self_test.py
echo
echo "Now run: python main.py"
