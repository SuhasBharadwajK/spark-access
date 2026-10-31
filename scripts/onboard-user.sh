#!/usr/bin/env bash
# Onboard a teammate onto the DGX Spark: Linux account, SSH key, optional LiteLLM key.
#
# Usage:
#   sudo ./onboard-user.sh <username> <public-key-file | "ssh-ed25519 AAAA...">
#
# Examples:
#   sudo ./onboard-user.sh priya ./priya_id_ed25519.pub
#   sudo ./onboard-user.sh priya "ssh-ed25519 AAAAC3Nza... anything"
#
# The key's comment is replaced with "<username>@spl", so authorized_keys
# always says whose key it is, whatever the teammate's laptop called it.
#
# Optional, to also create the person's LiteLLM key (skipped when unset):
#   export LITELLM_URL=http://127.0.0.1:4000   # when run on the Spark
#   export LITELLM_MASTER_KEY=sk-...
#   export LITELLM_MAX_PARALLEL=4            # default 4
#   export SPARK_EMAIL_DOMAIN=contoso.com    # user_id becomes <username>@<domain>
#
# Safe to re-run: an existing account is kept and the key is only added once.

set -euo pipefail

GROUP="spark-users"
KEY_COMMENT_SUFFIX="spl"

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ $# -eq 2 ]] || die "usage: sudo $0 <username> <public-key-file | \"ssh-ed25519 AAAA...\">"

USERNAME="$1"
KEY_INPUT="$2"

# Same rule Ubuntu's adduser uses for portable names.
[[ "$USERNAME" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || die "invalid username '$USERNAME' (lowercase letters, digits, - and _, max 32)"

# --- Public key -------------------------------------------------------------
if [[ -f "$KEY_INPUT" ]]; then
  ! grep -q "PRIVATE KEY" "$KEY_INPUT" || die "that is a PRIVATE key file; ask $USERNAME for the .pub file instead"
  KEY_LINE="$(grep -m1 -E '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com) ' "$KEY_INPUT" || true)"
else
  KEY_LINE="$KEY_INPUT"
fi
[[ -n "$KEY_LINE" ]] || die "no public key found in '$KEY_INPUT'"
[[ "$KEY_LINE" != *"PRIVATE KEY"* ]] || die "that is a PRIVATE key; ask $USERNAME for the .pub file instead"

read -r KEY_TYPE KEY_BODY _ <<<"$KEY_LINE"
[[ "$KEY_TYPE" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com)$ ]] \
  || die "unsupported key type '$KEY_TYPE'"
[[ -n "${KEY_BODY:-}" ]] || die "public key has no key data"

KEY_LINE="$KEY_TYPE $KEY_BODY $USERNAME@$KEY_COMMENT_SUFFIX"

# Let ssh-keygen confirm it is a well-formed key.
TMP_KEY="$(mktemp)"
trap 'rm -f "$TMP_KEY"' EXIT
echo "$KEY_LINE" >"$TMP_KEY"
FINGERPRINT="$(ssh-keygen -l -f "$TMP_KEY" 2>/dev/null)" || die "ssh-keygen rejected the public key"
info "Key: $FINGERPRINT"

# --- Group and account ------------------------------------------------------
if ! getent group "$GROUP" >/dev/null; then
  info "Creating group $GROUP"
  groupadd "$GROUP"
fi

if id "$USERNAME" >/dev/null 2>&1; then
  info "User $USERNAME already exists, keeping it"
else
  info "Creating user $USERNAME (no password, SSH key only)"
  adduser --disabled-password --gecos "" "$USERNAME"
fi
usermod -aG "$GROUP" "$USERNAME"

# Re-enable an account that was offboarded earlier (shell, expiry, group).
usermod -s /bin/bash "$USERNAME"
chage -E -1 "$USERNAME"

HOME_DIR="$(getent passwd "$USERNAME" | cut -d: -f6)"
if [[ ! -d "$HOME_DIR" ]]; then
  info "Recreating missing home folder $HOME_DIR"
  cp -a /etc/skel "$HOME_DIR"
  chown -R "$USERNAME:$USERNAME" "$HOME_DIR"
fi
chmod 750 "$HOME_DIR"

# --- authorized_keys --------------------------------------------------------
SSH_DIR="$HOME_DIR/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"
install -d -m 700 -o "$USERNAME" -g "$USERNAME" "$SSH_DIR"
touch "$AUTH_KEYS"

if grep -qF "$KEY_BODY" "$AUTH_KEYS"; then
  info "Key already in $AUTH_KEYS"
else
  echo "$KEY_LINE" >>"$AUTH_KEYS"
  info "Added key to $AUTH_KEYS"
fi
chown "$USERNAME:$USERNAME" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

# --- LiteLLM key (optional) -------------------------------------------------
if [[ -n "${LITELLM_URL:-}" && -n "${LITELLM_MASTER_KEY:-}" ]]; then
  USER_ID="$USERNAME"
  [[ -n "${SPARK_EMAIL_DOMAIN:-}" ]] && USER_ID="$USERNAME@$SPARK_EMAIL_DOMAIN"
  info "Creating LiteLLM key for $USER_ID"
  RESPONSE="$(curl -sS --fail-with-body "$LITELLM_URL/key/generate" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\": \"$USER_ID\", \"key_alias\": \"$USERNAME\", \"max_parallel_requests\": ${LITELLM_MAX_PARALLEL:-4}}")" \
    || die "LiteLLM key creation failed: $RESPONSE"
  API_KEY="$(echo "$RESPONSE" | jq -r '.key')"
  echo
  echo "LiteLLM key for $USERNAME (share it through the password manager, then clear your terminal):"
  echo "  $API_KEY"
else
  info "LITELLM_URL / LITELLM_MASTER_KEY not set, skipping the LiteLLM key"
fi

# --- Remaining manual steps -------------------------------------------------
cat <<EOF

Done: Linux account '$USERNAME' in group '$GROUP', SSH key $USERNAME@$KEY_COMMENT_SUFFIX installed.

Still to do by hand:
  1. Tailscale admin console: approve $USERNAME's user and device; add them to group:spark-team in the policy.
  2. Entra ID: add them to the 'Spark Team' group (Grafana, JupyterHub, Open WebUI).
  3. Add them to the 'Spark' booking calendar and team channel.
  4. Send them the teammate laptop steps (Step 6 of the access walkthrough).

Test from their laptop:  ssh $USERNAME@spark
EOF
