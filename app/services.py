"""One adapter per system. Each provision/deprovision returns a Result and never raises,
so one system being down doesn't stop the others; the page shows what happened where."""

import secrets
import string
import subprocess
from dataclasses import dataclass, field

import httpx

from . import config


@dataclass
class Result:
    system: str
    ok: bool
    message: str
    secrets: dict[str, str] = field(default_factory=dict)   # shown once, never stored
    ref: str | int | None = None                             # id in that system


def _password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _err(system: str, exc: Exception) -> Result:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:300]
        return Result(system, False, f"HTTP {exc.response.status_code}: {body}")
    return Result(system, False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------- Linux ---

def _run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    # sudo -n: never prompt. The sudoers rule allows exactly these two scripts.
    return subprocess.run(
        ["sudo", "-n", f"{config.SCRIPTS_DIR}/{name}", *args],
        capture_output=True, text=True, timeout=120,
    )


def linux_onboard(username: str, public_key: str) -> Result:
    if not config.LINUX_ENABLED:
        return Result("Linux", True, "skipped (LINUX_ENABLED=false)")
    try:
        proc = _run_script("onboard-user.sh", username, public_key)
    except Exception as exc:  # noqa: BLE001
        return _err("Linux", exc)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return Result("Linux", False, out.splitlines()[-1] if out else f"exit {proc.returncode}")
    return Result("Linux", True, f"account and SSH key {username}@spl installed")


def linux_offboard(username: str) -> Result:
    if not config.LINUX_ENABLED:
        return Result("Linux", True, "skipped (LINUX_ENABLED=false)")
    try:
        proc = _run_script("offboard-user.sh", username, "--yes")
    except Exception as exc:  # noqa: BLE001
        return _err("Linux", exc)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return Result("Linux", False, out.splitlines()[-1] if out else f"exit {proc.returncode}")
    return Result("Linux", True, "account locked, SSH key disabled, home archived")


# ------------------------------------------------------------------- LiteLLM ---

def _litellm() -> httpx.Client:
    return httpx.Client(
        base_url=config.LITELLM_URL, timeout=20,
        headers={"Authorization": f"Bearer {config.LITELLM_MASTER_KEY}"},
    )


def litellm_onboard(username: str, email: str | None) -> Result:
    if not config.LITELLM_MASTER_KEY:
        return Result("LiteLLM", True, "skipped (no LITELLM_MASTER_KEY)")
    try:
        with _litellm() as c:
            r = c.post("/key/generate", json={
                "key_alias": username,
                "user_id": email or username,
                "max_parallel_requests": config.LITELLM_MAX_PARALLEL,
            })
            r.raise_for_status()
            key = r.json()["key"]
    except Exception as exc:  # noqa: BLE001
        return _err("LiteLLM", exc)
    return Result("LiteLLM", True, f"key '{username}' created", {"LiteLLM API key": key}, username)


def litellm_offboard(username: str) -> Result:
    if not config.LITELLM_MASTER_KEY:
        return Result("LiteLLM", True, "skipped (no LITELLM_MASTER_KEY)")
    try:
        with _litellm() as c:
            r = c.post("/key/delete", json={"key_aliases": [username]})
            r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("LiteLLM", exc)
    return Result("LiteLLM", True, f"key '{username}' revoked")


# ----------------------------------------------------------------- Portainer ---

def _portainer() -> httpx.Client:
    return httpx.Client(
        base_url=config.PORTAINER_URL, timeout=20, verify=config.PORTAINER_VERIFY_TLS,
        headers={"X-API-Key": config.PORTAINER_API_KEY},
    )


def _portainer_user_id(c: httpx.Client, username: str) -> int | None:
    r = c.get("/api/users")
    r.raise_for_status()
    for u in r.json():
        if u.get("Username", "").lower() == username.lower():
            return u["Id"]
    return None


def _portainer_team_id(c: httpx.Client) -> int:
    r = c.get("/api/teams")
    r.raise_for_status()
    for t in r.json():
        if t.get("Name") == config.PORTAINER_TEAM:
            return t["Id"]
    r = c.post("/api/teams", json={"name": config.PORTAINER_TEAM})
    r.raise_for_status()
    return r.json()["Id"]


def portainer_onboard(username: str) -> Result:
    if not config.PORTAINER_API_KEY:
        return Result("Portainer", True, "skipped (no PORTAINER_API_KEY)")
    try:
        with _portainer() as c:
            password = _password()
            user_id = _portainer_user_id(c, username)
            created = user_id is None
            if created:
                # role 2 = standard user: sees only what their team is given access to.
                r = c.post("/api/users", json={"username": username, "password": password, "role": 2})
                r.raise_for_status()
                user_id = r.json()["Id"]
            team_id = _portainer_team_id(c)
            r = c.post("/api/team_memberships", json={"userID": user_id, "teamID": team_id, "role": 2})
            if r.status_code not in (200, 409):   # 409: already a member
                r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("Portainer", exc)
    if not created:
        return Result("Portainer", True, f"existing user kept, in team '{config.PORTAINER_TEAM}'", ref=user_id)
    return Result("Portainer", True, f"standard user in team '{config.PORTAINER_TEAM}'",
                  {"Portainer password": password}, user_id)


def portainer_offboard(username: str) -> Result:
    if not config.PORTAINER_API_KEY:
        return Result("Portainer", True, "skipped (no PORTAINER_API_KEY)")
    try:
        with _portainer() as c:
            user_id = _portainer_user_id(c, username)
            if user_id is None:
                return Result("Portainer", True, "no such user (nothing to remove)")
            c.delete(f"/api/users/{user_id}").raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("Portainer", exc)
    return Result("Portainer", True, "user deleted")


# ----------------------------------------------------------------- Turnstone ---

def _turnstone() -> httpx.Client:
    return httpx.Client(
        base_url=config.TURNSTONE_URL, timeout=20, verify=config.TURNSTONE_VERIFY_TLS,
        headers={"Authorization": f"Bearer {config.TURNSTONE_ADMIN_TOKEN}"},
    )


def _turnstone_user_id(c: httpx.Client, username: str) -> str | None:
    r = c.get("/v1/api/admin/users")
    r.raise_for_status()
    for u in r.json().get("users", []):
        if u.get("username", "").lower() == username.lower():
            return u["user_id"]
    return None


def _turnstone_role_id(c: httpx.Client) -> str:
    """TURNSTONE_ROLE may be a role's name (custom roles get a random role_id) or its role_id."""
    wanted = config.TURNSTONE_ROLE
    r = c.get("/v1/api/admin/roles")
    if r.status_code == 403:          # token can't list roles: treat the setting as a role_id
        return wanted
    r.raise_for_status()
    for role in r.json().get("roles", []):
        if wanted in (role.get("role_id"), role.get("name")):
            return role["role_id"]
    raise LookupError(f"Turnstone has no role named '{wanted}' (check TURNSTONE_ROLE)")


def turnstone_onboard(username: str, display_name: str) -> Result:
    if not config.TURNSTONE_ADMIN_TOKEN:
        return Result("Turnstone", True, "skipped (no TURNSTONE_ADMIN_TOKEN)")
    try:
        with _turnstone() as c:
            password = _password()
            role_id = _turnstone_role_id(c)   # before creating, so a bad role name creates nothing
            user_id = _turnstone_user_id(c, username)
            created = user_id is None
            if created:
                r = c.post("/v1/api/admin/users", json={
                    "username": username, "display_name": display_name, "password": password,
                })
                r.raise_for_status()
                user_id = r.json()["user_id"]
            # Users created through the API have no role, and Turnstone refuses logins
            # with an empty permission set, so a role is required.
            r = c.post(f"/v1/api/admin/users/{user_id}/roles", json={"role_id": role_id})
            if r.status_code not in (200, 201, 409):
                r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("Turnstone", exc)
    if not created:
        return Result("Turnstone", True, f"existing user kept, role {config.TURNSTONE_ROLE}", ref=user_id)
    return Result("Turnstone", True, f"user created with role {config.TURNSTONE_ROLE}",
                  {"Turnstone password": password}, user_id)


def turnstone_offboard(username: str) -> Result:
    if not config.TURNSTONE_ADMIN_TOKEN:
        return Result("Turnstone", True, "skipped (no TURNSTONE_ADMIN_TOKEN)")
    try:
        with _turnstone() as c:
            user_id = _turnstone_user_id(c, username)
            if user_id is None:
                return Result("Turnstone", True, "no such user (nothing to remove)")
            # Deleting the user also revokes all their Turnstone API tokens.
            c.delete(f"/v1/api/admin/users/{user_id}").raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("Turnstone", exc)
    return Result("Turnstone", True, "user and tokens deleted")


# ---------------------------------------------------------------- health ----

def health() -> dict[str, str]:
    """Quick reachability check for the dashboard header."""
    status: dict[str, str] = {}
    checks = [
        ("LiteLLM", config.LITELLM_MASTER_KEY, lambda: httpx.get(f"{config.LITELLM_URL}/health/liveliness", timeout=3)),
        ("Portainer", config.PORTAINER_API_KEY, lambda: httpx.get(
            f"{config.PORTAINER_URL}/api/system/status", timeout=3, verify=config.PORTAINER_VERIFY_TLS)),
        ("Turnstone", config.TURNSTONE_ADMIN_TOKEN, lambda: httpx.get(
            f"{config.TURNSTONE_URL}/health", timeout=3, verify=config.TURNSTONE_VERIFY_TLS)),
        ("JupyterHub", config.JUPYTERHUB_USERS_FILE, lambda: httpx.get(
            f"{config.JUPYTERHUB_URL}/hub/api/", timeout=3)),
    ]
    for name, configured, call in checks:
        if not configured:
            status[name] = "not configured"
            continue
        try:
            status[name] = "up" if call().status_code < 500 else "error"
        except Exception:  # noqa: BLE001
            status[name] = "down"
    return status


# ---------------------------------------------------------------- JupyterHub ---
# The hub reads JUPYTERHUB_USERS_FILE on every sign-in and notebook start, so writing the
# file is what grants access and sets limits. The API is only used to take access away.

def jupyterhub_enabled() -> bool:
    return bool(config.JUPYTERHUB_USERS_FILE)


def jupyterhub_new_password() -> tuple[str, str]:
    from .security import hash_password
    password = _password()
    return password, hash_password(password)


def jupyterhub_sync() -> Result:
    """Rewrite the users file from the database (atomically, readable only by its owner)."""
    if not jupyterhub_enabled():
        return Result("JupyterHub", True, "skipped (no JUPYTERHUB_USERS_FILE)")
    import json
    import os
    from . import db

    users = {}
    if config.JUPYTERHUB_ADMIN and config.ADMIN_PASSWORD_HASH:
        users[config.JUPYTERHUB_ADMIN] = {
            "password": config.ADMIN_PASSWORD_HASH, "admin": True,
            "cpu": config.JUPYTERHUB_DEFAULT_CPU, "mem": f"{config.JUPYTERHUB_DEFAULT_MEM_GB}G",
        }
    for p in db.jupyterhub_people():
        if p["username"] == config.JUPYTERHUB_ADMIN:
            continue
        users[p["username"]] = {
            "password": p["jh_password_hash"], "admin": False,
            "cpu": p["jh_cpu"] or config.JUPYTERHUB_DEFAULT_CPU,
            "mem": f"{p['jh_mem_gb'] or config.JUPYTERHUB_DEFAULT_MEM_GB}G",
        }
    path = config.JUPYTERHUB_USERS_FILE
    tmp = f"{path}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"version": 1, "users": users}, f, indent=2, sort_keys=True)
        os.replace(tmp, path)   # the hub mounts the folder, so it sees the new file at once
    except OSError as exc:
        return _err("JupyterHub", exc)
    return Result("JupyterHub", True, f"users file updated ({len(users)} accounts)")


def _jupyterhub() -> httpx.Client:
    return httpx.Client(
        base_url=f"{config.JUPYTERHUB_URL}/hub/api", timeout=30,
        headers={"Authorization": f"token {config.JUPYTERHUB_API_TOKEN}"},
    )


def jupyterhub_running(username: str) -> bool | None:
    """True/False if the hub answers, None if it can't be asked."""
    if not config.JUPYTERHUB_API_TOKEN:
        return None
    try:
        with _jupyterhub() as c:
            r = c.get(f"/users/{username}")
            if r.status_code == 404:
                return False
            r.raise_for_status()
            return bool(r.json().get("server"))
    except Exception:  # noqa: BLE001
        return None


def jupyterhub_stop_server(username: str) -> Result:
    if not config.JUPYTERHUB_API_TOKEN:
        return Result("JupyterHub", False, "no JUPYTERHUB_API_TOKEN: stop it on the hub's Admin page")
    try:
        with _jupyterhub() as c:
            r = c.delete(f"/users/{username}/server")
            if r.status_code not in (202, 204, 400, 404):   # 400/404: not running / no such user
                r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("JupyterHub", exc)
    return Result("JupyterHub", True, "notebook stopped (if it was running)")


def jupyterhub_remove(username: str) -> Result:
    """Stop their notebook and delete them from the hub, which also ends their sessions.
    Their files stay in the Docker volume jupyterhub-user-<username>."""
    if not config.JUPYTERHUB_API_TOKEN:
        return Result("JupyterHub", True,
                      "removed from the users file; no JUPYTERHUB_API_TOKEN, so an open session "
                      "lasts until it expires (delete them on the hub's Admin page)")
    stop = jupyterhub_stop_server(username)
    if not stop.ok:
        return stop
    try:
        import time
        with _jupyterhub() as c:
            for _ in range(30):                           # wait for the stop to finish
                r = c.get(f"/users/{username}")
                if r.status_code == 404:
                    return Result("JupyterHub", True, "not on the hub (nothing to delete)")
                r.raise_for_status()
                if not r.json().get("server") and not r.json().get("pending"):
                    break
                time.sleep(1)
            r = c.delete(f"/users/{username}")
            if r.status_code not in (204, 404):
                r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err("JupyterHub", exc)
    return Result("JupyterHub", True, "notebook stopped and user deleted (files kept in their volume)")
