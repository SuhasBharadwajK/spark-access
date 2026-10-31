"""Admin password hashing (scrypt, standard library only) and CSRF tokens."""

import base64
import hashlib
import hmac
import secrets
import sys

N, R, P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                              n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def csrf_token(session: dict) -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_ok(session: dict, token: str) -> bool:
    return bool(token) and hmac.compare_digest(session.get("csrf", ""), token)


if __name__ == "__main__":
    # python -m app.security  -> prompts for a password, prints the hash for ADMIN_PASSWORD_HASH
    import getpass
    pw = getpass.getpass("New admin password: ")
    if len(pw) < 12 or pw != getpass.getpass("Repeat: "):
        sys.exit("Passwords differ or shorter than 12 characters.")
    # $ is special in systemd EnvironmentFile only inside quotes; print unquoted.
    print(hash_password(pw))
