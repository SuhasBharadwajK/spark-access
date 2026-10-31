#!/usr/bin/env bash
# Run the app straight from this clone, as your own user (no install.sh).
# One-time setup: see "Run from a clone" in README.md (venv, .env, sudoers line).
#
#   tmux new -s spark-access ./run-local.sh      # Ctrl+B then D to detach
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "No .env here: cp env.example .env and fill it in"; exit 1; }
[[ -x .venv/bin/uvicorn ]] || { echo "No .venv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[[ $EUID -ne 0 ]] || { echo "Don't run this as root; run it as your normal user"; exit 1; }
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8099 --env-file .env
