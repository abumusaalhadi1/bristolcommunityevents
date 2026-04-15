import os
from pathlib import Path


def load_env_file(path: Path):
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


config_dir = Path(__file__).resolve().parent
for env_path in (config_dir / ".env", config_dir.parent / ".env"):
    load_env_file(env_path)

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
DEFAULT_ADMIN_NAME = os.getenv("DEFAULT_ADMIN_NAME", "Administrator")
DEFAULT_ADMIN_EMAIL = os.getenv(
    "DEFAULT_ADMIN_EMAIL",
    ADMIN_USERNAME if "@" in ADMIN_USERNAME else "admin@bristol-events.local",
)
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)

PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip()

MAIL_SERVER = os.getenv("MAIL_SERVER", "").strip()
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USE_TLS = env_bool("MAIL_USE_TLS", True)
MAIL_USE_SSL = env_bool("MAIL_USE_SSL", False)
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").replace(" ", "").strip()
MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", MAIL_USERNAME).strip()
MAIL_SUPPRESS_SEND = env_bool("MAIL_SUPPRESS_SEND", False)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "Uwe#@#98714UK"),
    "database": os.getenv("DB_NAME", "bristol_community_events_db"),
}
