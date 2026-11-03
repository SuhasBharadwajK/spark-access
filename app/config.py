"""Settings, read from the environment (the systemd unit loads /etc/spark-access/env)."""

import os


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DB_PATH = os.environ.get("SPARK_ACCESS_DB", "/var/lib/spark-access/spark-access.db")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")

# Linux: the two scripts, run through a sudoers rule that allows exactly these.
SCRIPTS_DIR = os.environ.get("SCRIPTS_DIR", "/opt/spark-access/scripts")
LINUX_ENABLED = _bool("LINUX_ENABLED", True)

LITELLM_URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
LITELLM_MAX_PARALLEL = int(os.environ.get("LITELLM_MAX_PARALLEL", "4"))

PORTAINER_URL = os.environ.get("PORTAINER_URL", "https://127.0.0.1:9443").rstrip("/")
PORTAINER_API_KEY = os.environ.get("PORTAINER_API_KEY", "")
PORTAINER_TEAM = os.environ.get("PORTAINER_TEAM", "Developers")
PORTAINER_VERIFY_TLS = _bool("PORTAINER_VERIFY_TLS", False)  # Portainer uses a self-signed cert
PORTAINER_SANDBOX_ENV = os.environ.get("PORTAINER_SANDBOX_ENV", "Sandbox")  # offboarding deletes <user>-* stacks here

TURNSTONE_URL = os.environ.get("TURNSTONE_URL", "https://127.0.0.1:443").rstrip("/")
TURNSTONE_ADMIN_TOKEN = os.environ.get("TURNSTONE_ADMIN_TOKEN", "")
TURNSTONE_ROLE = os.environ.get("TURNSTONE_ROLE", "spl-dev")   # role name (or role_id) given to new users
TURNSTONE_VERIFY_TLS = _bool("TURNSTONE_VERIFY_TLS", False)  # Caddy's internal cert

# JupyterHub: spark-access writes the users file the hub reads (who may sign in, password hash,
# CPU and memory), and uses the hub API to stop servers and delete people who lose access.
JUPYTERHUB_USERS_FILE = os.environ.get("JUPYTERHUB_USERS_FILE", "")   # empty = JupyterHub not managed
JUPYTERHUB_URL = os.environ.get("JUPYTERHUB_URL", "http://127.0.0.1:8000").rstrip("/")
JUPYTERHUB_API_TOKEN = os.environ.get("JUPYTERHUB_API_TOKEN", "")
JUPYTERHUB_ADMIN = os.environ.get("JUPYTERHUB_ADMIN", "spl")         # signs in with the spark-access admin password
JUPYTERHUB_DEFAULT_CPU = float(os.environ.get("JUPYTERHUB_DEFAULT_CPU", "4"))
JUPYTERHUB_DEFAULT_MEM_GB = int(os.environ.get("JUPYTERHUB_DEFAULT_MEM_GB", "8"))
JUPYTERHUB_MAX_CPU = float(os.environ.get("JUPYTERHUB_MAX_CPU", "20"))
JUPYTERHUB_MAX_MEM_GB = int(os.environ.get("JUPYTERHUB_MAX_MEM_GB", "64"))

# Shown to new users next to their credentials.
OFFICE_BASE_URL = os.environ.get("OFFICE_BASE_URL", "http://spark:4000/v1")
TAILSCALE_BASE_URL = os.environ.get("TAILSCALE_BASE_URL", "http://spark:4000/v1")
TURNSTONE_PUBLIC_URL = os.environ.get("TURNSTONE_PUBLIC_URL", "https://spark")
PORTAINER_PUBLIC_URL = os.environ.get("PORTAINER_PUBLIC_URL", "https://spark:9443")
JUPYTERHUB_PUBLIC_URL = os.environ.get("JUPYTERHUB_PUBLIC_URL", "http://spark:8000")
