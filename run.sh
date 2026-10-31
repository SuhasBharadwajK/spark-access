#!/usr/bin/env bash
# Run the Spark access manager in the foreground (for tmux), as the spark-access user.
# Needs install.sh to have run once (user, venv, sudoers rule, /etc/spark-access/env).
#
#   tmux new -s spark-access
#   sudo /opt/spark-access/run.sh        # Ctrl+B then D to detach; tmux attach -t spark-access to come back
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo (it drops to the spark-access user)"; exit 1; }
exec sudo -u spark-access /opt/spark-access/venv/bin/uvicorn app.main:app \
  --app-dir /opt/spark-access --host 127.0.0.1 --port 8099 --env-file /etc/spark-access/env
