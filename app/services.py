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


def turnstone_onboard(username: str, display_name: str) -> Result:
    if not config.TURNSTONE_ADMIN_TOKEN:
        return Result("Turnstone", True, "skipped (no TURNSTONE_ADMIN_TOKEN)")
    try:
        with _turnstone() as c:
            password = _password()
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
            r = c.post(f"/v1/api/admin/users/{user_id}/roles", json={"role_id": config.TURNSTONE_ROLE})
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
