#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
    echo "FFmpeg and FFprobe are required on PATH."
    exit 1
fi
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r backend/requirements.txt

if [ ! -d "frontend/node_modules" ]; then
    npm --prefix frontend install
fi
# Always rebuild so code changes cannot silently leave a stale interface.
npm --prefix frontend run build

echo "AI Shorts Creator is available at http://localhost:8000"
export PYTHONPATH="$SCRIPT_DIR/backend"
exec .venv/bin/python backend/run.py
