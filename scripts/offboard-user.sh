#!/usr/bin/env bash
# Offboard a teammate from the DGX Spark: lock the account, cut SSH, stop their
# processes, archive their home folder, remove their Sandbox containers and volumes
# (names starting with <username>-), and optionally revoke their LiteLLM key.
#
# Usage:
#   sudo ./offboard-user.sh <username> [--delete-home] [--yes]
#
#   --delete-home   delete /home/<username> after archiving it (default: keep it)
#   --yes           don't ask for confirmation
#
# Optional, to also revoke the person's LiteLLM key (skipped when unset):
#   export LITELLM_URL=http://127.0.0.1:4000   # when run on the Spark
#   export LITELLM_MASTER_KEY=sk-...
#
# Archives go to /root/offboarded/<username>-<date>.tar.gz.
# The account itself is locked, not deleted, so file ownership stays readable;
# run `sudo deluser <username>` later once you no longer need it.

set -euo pipefail

GROUP="spark-users"
ARCHIVE_DIR="/root/offboarded"

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ $# -ge 1 ]] || die "usage: sudo $0 <username> [--delete-home] [--yes]"

USERNAME="$1"; shift
DELETE_HOME=false
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --delete-home) DELETE_HOME=true ;;
    --yes)         ASSUME_YES=true ;;
    *)             die "unknown option '$arg'" ;;
  esac
done

[[ "$USERNAME" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || die "invalid username '$USERNAME'"
id "$USERNAME" >/dev/null 2>&1 || die "user '$USERNAME' does not exist"

# Guard rails: never offboard root, system accounts, admins or yourself.
USER_UID="$(id -u "$USERNAME")"
(( USER_UID >= 1000 )) || die "'$USERNAME' is a system account (uid $USER_UID)"
[[ "$USERNAME" != "${SUDO_USER:-}" ]] || die "refusing to offboard yourself"
if id -nG "$USERNAME" | tr ' ' '\n' | grep -qxE 'sudo|admin|root'; then
  die "'$USERNAME' is an admin (sudo group); remove them from sudo by hand first if this is intended"
fi

HOME_DIR="$(getent passwd "$USERNAME" | cut -d: -f6)"

if ! $ASSUME_YES; then
  echo "About to offboard '$USERNAME' (home: $HOME_DIR, delete home: $DELETE_HOME)."
  read -r -p "Type the username to confirm: " CONFIRM
  [[ "$CONFIRM" == "$USERNAME" ]] || die "confirmation did not match, nothing changed"
fi

# --- Cut access -------------------------------------------------------------
info "Locking account and disabling login shell"
usermod -L -s /usr/sbin/nologin "$USERNAME"
chage -E 0 "$USERNAME"          # expire the account as well, so key logins fail too

info "Removing from $GROUP (sshd AllowGroups)"
gpasswd -d "$USERNAME" "$GROUP" >/dev/null 2>&1 || true

AUTH_KEYS="$HOME_DIR/.ssh/authorized_keys"
if [[ -f "$AUTH_KEYS" ]]; then
  info "Disabling SSH keys"
  mv "$AUTH_KEYS" "$AUTH_KEYS.disabled-$(date +%F)"
fi

info "Stopping their processes and sessions"
pkill -KILL -u "$USERNAME" 2>/dev/null || true
loginctl terminate-user "$USERNAME" 2>/dev/null || true

# Jupyter containers started for this user (DockerSpawner names them jupyter-<user>).
if command -v docker >/dev/null; then
  for c in $(docker ps -q --filter "name=^jupyter-$USERNAME$" 2>/dev/null); do
    info "Stopping Jupyter container $c"
    docker stop "$c" >/dev/null
  done
fi

# --- Sandbox (rootless Docker of the 'sandbox' user) ------------------------
# Everything there named <username>-...: containers, the volumes holding NIM model caches, networks.
# spark-access deletes their Portainer stacks first; this removes what a stack delete leaves behind
# (volumes) and any loose containers that follow the naming rule.
SANDBOX_SUMMARY="none"
if id sandbox >/dev/null 2>&1; then
  SB_UID="$(id -u sandbox)"
  SB_SOCK="/run/user/$SB_UID/docker.sock"
  if [[ -S "$SB_SOCK" ]]; then
    sbd() {
      sudo -u sandbox -H env XDG_RUNTIME_DIR="/run/user/$SB_UID" DOCKER_HOST="unix://$SB_SOCK" \
        PATH="/usr/bin:/usr/sbin:/bin" docker "$@"
    }
    # Other people whose username starts with "<username>-" (e.g. al and al-x): leave theirs alone.
    OTHERS="$(getent group "$GROUP" | cut -d: -f4 | tr ',' '\n' | grep -E "^$USERNAME-" || true)"
    mine() {
      local name="$1" o
      [[ "$name" == "$USERNAME-"* ]] || return 1
      for o in $OTHERS; do [[ "$name" == "$o-"* ]] && return 1; done
      return 0
    }
    n_c=0; n_v=0; n_n=0
    while read -r id name project; do
      [[ -n "$id" ]] || continue
      if mine "$name" || mine "${project:-}"; then
        info "Sandbox: removing container $name"
        sbd rm -f "$id" >/dev/null && n_c=$((n_c + 1))
      fi
    done < <(sbd ps -a --format '{{.ID}} {{.Names}} {{.Label "com.docker.compose.project"}}' 2>/dev/null)
    while read -r vol; do
      [[ -n "$vol" ]] && mine "$vol" || continue
      info "Sandbox: removing volume $vol"
      sbd volume rm "$vol" >/dev/null && n_v=$((n_v + 1))
    done < <(sbd volume ls -q 2>/dev/null)
    while read -r net; do
      [[ -n "$net" ]] && mine "$net" || continue
      sbd network rm "$net" >/dev/null 2>&1 && n_n=$((n_n + 1))
    done < <(sbd network ls --format '{{.Name}}' 2>/dev/null)
    SANDBOX_SUMMARY="$n_c containers, $n_v volumes, $n_n networks"
  else
    info "Sandbox daemon not running, skipping sandbox cleanup"
  fi
fi

# --- LiteLLM key (optional) -------------------------------------------------
if [[ -n "${LITELLM_URL:-}" && -n "${LITELLM_MASTER_KEY:-}" ]]; then
  info "Revoking LiteLLM key with alias '$USERNAME'"
  if ! curl -sS --fail-with-body "$LITELLM_URL/key/delete" \
       -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
       -H "Content-Type: application/json" \
       -d "{\"key_aliases\": [\"$USERNAME\"]}"; then
    echo
    echo "WARNING: could not revoke by alias. Find the key in the LiteLLM UI and delete it there,"
    echo "         or: curl -X POST \$LITELLM_URL/key/delete -H \"Authorization: Bearer \$LITELLM_MASTER_KEY\" \\"
    echo "               -H 'Content-Type: application/json' -d '{\"keys\": [\"sk-...\"]}'"
  fi
  echo
else
  info "LITELLM_URL / LITELLM_MASTER_KEY not set, skipping the LiteLLM key (revoke it by hand)"
fi

# --- Archive home -----------------------------------------------------------
ARCHIVE="(none: $HOME_DIR does not exist)"
if [[ -d "$HOME_DIR" ]]; then
  install -d -m 700 "$ARCHIVE_DIR"
  ARCHIVE="$ARCHIVE_DIR/$USERNAME-$(date +%F).tar.gz"
  info "Archiving $HOME_DIR to $ARCHIVE"
  tar -czf "$ARCHIVE" -C "$(dirname "$HOME_DIR")" "$(basename "$HOME_DIR")"
  chmod 600 "$ARCHIVE"
fi

if $DELETE_HOME && [[ -d "$HOME_DIR" ]]; then
  info "Deleting $HOME_DIR"
  rm -rf --one-file-system "$HOME_DIR"
fi

# --- Remaining manual steps -------------------------------------------------
cat <<EOF

Done: '$USERNAME' is locked out of SSH, their processes are stopped, home archived to $ARCHIVE.
Sandbox: removed $SANDBOX_SUMMARY named $USERNAME-*.

Still to do by hand (these are what really cut access):
  1. Entra ID: remove them from 'Spark Team' (closes Grafana, JupyterHub, Open WebUI).
  2. Tailscale admin console: Users -> remove $USERNAME (removes network access and their devices).
     Also remove them from group:spark-team in the policy file.
  3. JupyterHub admin page: delete their user and server.
  4. If they knew any shared secret (Portainer deputy password, LiteLLM master key), rotate it.
EOF
