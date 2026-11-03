# Spark access manager

One admin page on the DGX Spark that adds or removes a teammate across all five systems at once:

| System | On "Add a person" | On "Offboard" |
| --- | --- | --- |
| Linux (SSH) | Account in `spark-users`, SSH key installed as `<username>@spl` (runs `onboard-user.sh`) | Account locked and expired, key disabled, processes stopped, home archived; in the Sandbox Docker, containers, volumes and networks named `<username>-*` removed (runs `offboard-user.sh`) |
| LiteLLM | Personal key named `<username>`, user ID = work email if given | Key revoked |
| Portainer (CE) | Standard user, added to the `Developers` team | Their stacks in the Sandbox environment (named `<username>-*`, or private to them) deleted, then the user deleted |
| Turnstone | User with role `spl-dev` (`TURNSTONE_ROLE`; users created through the API get no role, and Turnstone refuses logins without one) | User deleted, which also revokes their Turnstone API tokens |
| JupyterHub (optional, ticked per person) | Can sign in, with the CPU cores and memory you set | Notebook stopped, user deleted on the hub (files stay in their volume) |

Each person's **JupyterHub access** link on the dashboard lets you change their cores and memory later,
give them a new JupyterHub password, or take JupyterHub away without offboarding them.

New passwords (Portainer, Turnstone, JupyterHub) and the LiteLLM key are shown **once** on the result page, never stored.
Every action is written to an audit log shown on the dashboard.

## How it is locked down

- Runs natively as a systemd service under its own `spark-access` system user, not in Docker.
- Listens on `127.0.0.1:8099` only. You open it through an SSH tunnel, so it is never on the office network.
- Its only root power is a sudoers rule allowing exactly `onboard-user.sh` and `offboard-user.sh` (root-owned copies in `/opt/spark-access/scripts`). Tested: any other `sudo` command is refused.
- Admin sign-in with a scrypt-hashed password, CSRF tokens on every form, 8-hour sessions.
- Secrets (LiteLLM master key, Portainer and Turnstone admin tokens) live in `/etc/spark-access/env`, readable only by root and the service.

## Install (on the Spark, about 15 minutes)

1. Clone this repository on the Spark (the installer takes the two scripts from `scripts/`).
2. Run the installer:
   ```bash
   cd spark-access
   sudo ./install.sh                 # or --no-service, then run.sh in tmux
   ```
3. Create the admin password hash and a session secret:
   ```bash
   cd /opt/spark-access && sudo ./venv/bin/python -m app.security   # prompts for a password (12+ chars)
   openssl rand -hex 32
   ```
4. Get the three admin credentials:
   - **LiteLLM:** the master key from the gateway stack.
   - **Portainer:** sign in as admin, **My account → Access tokens → Add access token**.
   - **Turnstone:** an API token for a Turnstone admin with scopes `read,write,approve`. Either in the console UI
     (admin user → tokens), or from the console container:
     ```bash
     docker exec -it <turnstone-console-container> turnstone-admin list-users
     docker exec -it <turnstone-console-container> turnstone-admin create-token \
       --user <admin user_id> --scopes read,write,approve --name spark-access
     ```
5. Edit `/etc/spark-access/env` (`sudo nano /etc/spark-access/env`): paste the hash, secret and the three credentials,
   and set the URLs shown to new people (`OFFICE_BASE_URL` and friends). Leave a credential empty to skip that system.
6. Start it:
   ```bash
   sudo systemctl start spark-access
   systemctl status spark-access
   ```
7. From your laptop:
   ```bash
   ssh -L 8099:127.0.0.1:8099 <you>@spark
   ```
   then open <http://localhost:8099>.

To update later: copy the new folder over and run `sudo ./install.sh` again. Config and the database are kept.

## Run from a clone, as your own user (no install)

For running it in tmux as `spl` straight from this repository. Everything stays in the clone; the only
system change is one sudoers line.

```bash
sudo apt install -y python3-venv jq tmux
git clone git@github.com:SuhasBharadwajK/spark-access.git ~/spark-access
cd ~/spark-access

python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp env.example .env && chmod 600 .env
.venv/bin/python -m app.security          # admin password hash
openssl rand -hex 32                      # session secret
nano .env
```

In `.env`, besides the hash, secret, keys and URLs, set:

```
SPARK_ACCESS_DB=/home/spl/spark-access/spark-access.db
SCRIPTS_DIR=/home/spl/spark-access/scripts
```

Let `spl` run just the two scripts without a password (the app uses `sudo -n`, which never prompts):

```bash
echo "spl ALL=(root) NOPASSWD: /home/spl/spark-access/scripts/onboard-user.sh *, /home/spl/spark-access/scripts/offboard-user.sh *" \
  | sudo tee /etc/sudoers.d/spark-access
sudo chmod 440 /etc/sudoers.d/spark-access && sudo visudo -c
```

Start it, then open it from your laptop with `ssh -L 8099:127.0.0.1:8099 spl@<spark>` and <http://localhost:8099>:

```bash
tmux new -s spark-access ./run-local.sh   # Ctrl+B then D to detach; tmux attach -t spark-access to return
```

To update: `git pull`, then restart the tmux session (Ctrl+C, `./run-local.sh`).

## Connecting JupyterHub

spark-access decides who can use JupyterHub: it writes `users.json` (username, password hash, CPU, memory) into
a folder that the hub reads on every sign-in and notebook start. The hub's admin account (`JUPYTERHUB_ADMIN`,
default `spl`) signs in with **this app's admin password**.

1. Make the folder before deploying the `jupyterhub` stack, so that `spl` owns it (if Docker creates it, root does):
   ```bash
   mkdir -p ~/jupyterhub/access
   openssl rand -hex 32          # the API token, used in both places below
   ```
2. In the `jupyterhub` stack's environment variables: `SPARK_ACCESS_API_TOKEN=<token>`.
3. In spark-access's `.env`:
   ```
   JUPYTERHUB_USERS_FILE=/home/spl/jupyterhub/access/users.json
   JUPYTERHUB_URL=http://127.0.0.1:8000
   JUPYTERHUB_API_TOKEN=<token>
   JUPYTERHUB_PUBLIC_URL=http://tony-spark.local:8000
   ```
   Defaults and ceilings for the form: `JUPYTERHUB_DEFAULT_CPU=4`, `JUPYTERHUB_DEFAULT_MEM_GB=8`,
   `JUPYTERHUB_MAX_CPU=20`, `JUPYTERHUB_MAX_MEM_GB=64`.
4. Restart spark-access. It writes `users.json` straight away, so you can sign in to the hub as `spl`.

New limits apply when that person's notebook next starts. The edit page can stop a running notebook for you.
Without the API token, access still follows the file, but an open browser session lasts until it expires and
you stop servers on the hub's Admin page yourself.

## One-time Portainer setup (Community Edition)

The app puts everyone in the `Developers` team (created on first use). In CE, a team member sees and controls only
the resources shared with their team, so decide what they get:

- For each stack or container they may manage: open it in Portainer → **Access control** → **Restricted** →
  add the **Developers** team.
- CE has no fine-grained roles: whatever a team can see, it can start, stop, recreate and delete. Share the models
  stack only if you are happy for them to turn models on and off; keep `gateway` and `monitoring` admin-only.

This is how people get "limited Docker": through Portainer, not the `docker` group (which would be root on the box).

## Files

| Path | What |
| --- | --- |
| `/opt/spark-access/app` | The app (FastAPI + Jinja templates) |
| `/opt/spark-access/scripts` | Root-owned copies of `scripts/onboard-user.sh` and `scripts/offboard-user.sh` |
| `/etc/spark-access/env` | Settings and secrets |
| `/var/lib/spark-access/spark-access.db` | People and audit log (SQLite); include it in backups |
| `/etc/sudoers.d/spark-access` | The two-script sudo rule |

## If something fails

Each system is handled separately, so one being down doesn't stop the others. The result page and the dashboard
show which system failed and why (hover the red cell). Fix the cause, then either finish that system by hand, or
offboard the person and add them again.

Still manual, by design: Tailscale invites (for the three remote users) and Entra ID groups, if you add SSO later.
