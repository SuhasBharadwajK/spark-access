"""Spark access manager: add and remove a teammate across Linux, LiteLLM, Portainer and
Turnstone from one page. Listens on 127.0.0.1 only; reach it over SSH or Tailscale."""

import re
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, services
from .security import csrf_ok, csrf_token, verify_password

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
KEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( .*)?$")

if not config.SESSION_SECRET or not config.ADMIN_PASSWORD_HASH:
    raise SystemExit("SESSION_SECRET or ADMIN_PASSWORD_HASH is empty: set both in the env file "
                     "(.env for run-local.sh, /etc/spark-access/env for the service)")

app = FastAPI(title="Spark access", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET,
                   session_cookie="spark_access", max_age=8 * 3600, same_site="strict")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
db.init()


def _admin(request: Request) -> str | None:
    return request.session.get("admin")


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.update(request=request, admin=_admin(request), csrf=csrf_token(request.session), cfg=config)
    return templates.TemplateResponse(request, name, ctx)


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- sign-in ---

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _page(request, "login.html", error=None)


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...),
          csrf: str = Form("")):
    if not csrf_ok(request.session, csrf):
        return _page(request, "login.html", error="Session expired, try again.")
    if username == config.ADMIN_USERNAME and verify_password(password, config.ADMIN_PASSWORD_HASH):
        request.session.clear()
        request.session["admin"] = username
        db.log(username, "login", "-", "admin signed in")
        return RedirectResponse("/", status_code=303)
    db.log(username or "?", "login-failed", "-", "wrong username or password")
    return _page(request, "login.html", error="Wrong username or password.")


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    if csrf_ok(request.session, csrf):
        request.session.clear()
    return _login_redirect()


# -------------------------------------------------------------- dashboard ---

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _admin(request):
        return _login_redirect()
    return _page(request, "index.html", people=db.list_people(),
                 health=services.health(), audit=db.recent_audit(30))


# ------------------------------------------------------------- onboarding ---

@app.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request):
    if not _admin(request):
        return _login_redirect()
    return _page(request, "new_user.html", error=None, form={})


@app.post("/users", response_class=HTMLResponse)
def create_user(request: Request, username: str = Form(...), display_name: str = Form(...),
                email: str = Form(""), public_key: str = Form(...), csrf: str = Form("")):
    admin = _admin(request)
    if not admin:
        return _login_redirect()
    form = {"username": username, "display_name": display_name, "email": email, "public_key": public_key}
    username, display_name, email = username.strip().lower(), display_name.strip(), email.strip()
    public_key = " ".join(public_key.split())

    error = None
    if not csrf_ok(request.session, csrf):
        error = "Session expired, reload the page."
    elif not USERNAME_RE.match(username):
        error = "Username: lowercase letters, digits, - and _, starting with a letter, max 32."
    elif not display_name:
        error = "Display name is required."
    elif "PRIVATE KEY" in public_key:
        error = "That is a private key. Ask for the .pub file instead."
    elif not KEY_RE.match(public_key):
        error = "That doesn't look like an SSH public key (ssh-ed25519 AAAA...)."
    elif (p := db.get_person(username)) and p["status"] == "active":
        error = f"'{username}' is already active. Offboard them first to recreate."
    if error:
        return _page(request, "new_user.html", error=error, form=form)

    results = [
        services.linux_onboard(username, public_key),
        services.litellm_onboard(username, email or None),
        services.portainer_onboard(username),
        services.turnstone_onboard(username, display_name),
    ]
    by = {r.system: r for r in results}
    db.upsert_person(
        username=username, display_name=display_name, email=email or None,
        key_comment=f"{username}@spl", status="active",
        linux="ok" if by["Linux"].ok else by["Linux"].message,
        litellm="ok" if by["LiteLLM"].ok else by["LiteLLM"].message,
        portainer="ok" if by["Portainer"].ok else by["Portainer"].message,
        portainer_id=by["Portainer"].ref if isinstance(by["Portainer"].ref, int) else None,
        turnstone="ok" if by["Turnstone"].ok else by["Turnstone"].message,
        turnstone_id=str(by["Turnstone"].ref) if by["Turnstone"].ref else None,
    )
    for r in results:
        db.log(admin, "onboard", username, f"{r.system}: {'ok' if r.ok else 'FAILED'} - {r.message}")
    secrets_out = {k: v for r in results for k, v in r.secrets.items()}
    return _page(request, "result.html", action="Onboarded", username=username,
                 results=results, secrets=secrets_out)


# ------------------------------------------------------------ offboarding ---

@app.post("/users/{username}/offboard", response_class=HTMLResponse)
def offboard_user(request: Request, username: str, confirm: str = Form(""), csrf: str = Form("")):
    admin = _admin(request)
    if not admin:
        return _login_redirect()
    person = db.get_person(username)
    if not person or not csrf_ok(request.session, csrf) or confirm.strip() != username:
        return _page(request, "index.html", people=db.list_people(), health=services.health(),
                     audit=db.recent_audit(30),
                     error=f"Not offboarded: type '{username}' exactly in the confirm box.")
    results = [
        services.turnstone_offboard(username),
        services.portainer_offboard(username),
        services.litellm_offboard(username),
        services.linux_offboard(username),
    ]
    by = {r.system: r for r in results}
    db.upsert_person(
        username=username, display_name=person["display_name"], status="offboarded",
        linux="removed" if by["Linux"].ok else by["Linux"].message,
        litellm="removed" if by["LiteLLM"].ok else by["LiteLLM"].message,
        portainer="removed" if by["Portainer"].ok else by["Portainer"].message,
        turnstone="removed" if by["Turnstone"].ok else by["Turnstone"].message,
    )
    for r in results:
        db.log(admin, "offboard", username, f"{r.system}: {'ok' if r.ok else 'FAILED'} - {r.message}")
    return _page(request, "result.html", action="Offboarded", username=username,
                 results=results, secrets={})
