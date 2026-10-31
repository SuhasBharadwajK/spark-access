#!/usr/bin/env bash
# Install the Spark access manager on the DGX Spark. Run from this folder:
#   sudo ./install.sh               # installs and enables the systemd service
#   sudo ./install.sh --no-service  # everything except systemd; start it with run.sh (e.g. in tmux)
# Takes onboard-user.sh and offboard-user.sh from ./scripts.
# Safe to re-run: updates the app and scripts, keeps /etc/spark-access/env and the database.

set -euo pipefail
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"
NO_SERVICE=false
[[ "${1:-}" == "--no-service" ]] && NO_SERVICE=true
SRC="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_SRC="$SRC/scripts"
for f in onboard-user.sh offboard-user.sh; do
  [[ -f "$SCRIPTS_SRC/$f" ]] || die "missing $SCRIPTS_SRC/$f"
done

APP=/opt/spark-access
ETC=/etc/spark-access
DATA=/var/lib/spark-access
SVC_USER=spark-access

info "Packages"
apt-get install -y -q python3-venv sudo jq >/dev/null

info "Service user $SVC_USER"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin "$SVC_USER"

info "App files in $APP"
install -d -m 755 "$APP" "$APP/scripts"
rm -rf "$APP/app"
cp -r "$SRC/app" "$APP/app"
cp "$SRC/requirements.txt" "$APP/"
install -m 755 "$SRC/run.sh" "$APP/run.sh"
chown -R root:root "$APP"
chmod -R go-w "$APP"

# The scripts run as root through sudo, so they must be root-owned and not writable by anyone else.
install -m 755 -o root -g root "$SCRIPTS_SRC/onboard-user.sh"  "$APP/scripts/onboard-user.sh"
install -m 755 -o root -g root "$SCRIPTS_SRC/offboard-user.sh" "$APP/scripts/offboard-user.sh"

info "Python environment"
[[ -x "$APP/venv/bin/python" ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

info "Data folder $DATA"
install -d -m 700 -o "$SVC_USER" -g "$SVC_USER" "$DATA"

info "Config $ETC/env"
install -d -m 750 -o root -g "$SVC_USER" "$ETC"
if [[ ! -f "$ETC/env" ]]; then
  install -m 640 -o root -g "$SVC_USER" "$SRC/env.example" "$ETC/env"
  NEW_ENV=1
fi

info "sudoers rule: $SVC_USER may run only the two scripts"
SUDOERS=/etc/sudoers.d/spark-access
cat >"$SUDOERS.tmp" <<RULE
# Spark access manager: run only the onboarding/offboarding scripts as root.
$SVC_USER ALL=(root) NOPASSWD: $APP/scripts/onboard-user.sh *, $APP/scripts/offboard-user.sh *
RULE
visudo -cf "$SUDOERS.tmp" >/dev/null || die "sudoers rule failed validation"
install -m 440 -o root -g root "$SUDOERS.tmp" "$SUDOERS"
rm -f "$SUDOERS.tmp"

if $NO_SERVICE; then
  info "Skipping systemd (--no-service)."
  echo "Fill in $ETC/env (README steps 3 to 5), then:  tmux new -s spark-access 'sudo $APP/run.sh'"
  exit 0
fi

info "systemd unit"
install -m 644 "$SRC/spark-access.service" /etc/systemd/system/spark-access.service
systemctl daemon-reload
systemctl enable spark-access >/dev/null

if [[ "${NEW_ENV:-0}" == 1 ]] || ! grep -q '^ADMIN_PASSWORD_HASH=.\+' "$ETC/env" || ! grep -q '^SESSION_SECRET=.\+' "$ETC/env"; then
  cat <<NEXT

Installed, not started yet. Next:
  1. Admin password hash:  cd $APP && sudo $APP/venv/bin/python -m app.security
  2. Session secret:       openssl rand -hex 32
  3. Edit $ETC/env: paste both, plus the LiteLLM, Portainer and Turnstone keys.
  4. sudo systemctl start spark-access && systemctl status spark-access
  5. From your laptop:     ssh -L 8099:127.0.0.1:8099 <you>@spark   then open http://localhost:8099
NEXT
else
  systemctl restart spark-access
  info "Restarted. Open it through: ssh -L 8099:127.0.0.1:8099 <you>@spark -> http://localhost:8099"
fi
