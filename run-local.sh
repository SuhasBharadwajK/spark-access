#!/usr/bin/env bash
# Run the app straight from this clone, as your own user (no install.sh).
# One-time setup: see "Run from a clone" in README.md (venv, .env, sudoers line).
#
#   tmux new -s spark-access ./run-local.sh      # Ctrl+B then D to detach
#
# By default it listens on 127.0.0.1 (reach it with an SSH tunnel). To open it on the
# office network as http://<spark>.local:8099 instead, set SPARK_ACCESS_HOST=0.0.0.0
# and limit port 8099 to your own machine with ufw (see README).
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "No .env here: cp env.example .env and fill it in"; exit 1; }
[[ -x .venv/bin/uvicorn ]] || { echo "No .venv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[[ $EUID -ne 0 ]] || { echo "Don't run this as root; run it as your normal user"; exit 1; }
exec .venv/bin/uvicorn app.main:app --host "${SPARK_ACCESS_HOST:-127.0.0.1}" \
  --port "${SPARK_ACCESS_PORT:-8099}" --env-file .env
