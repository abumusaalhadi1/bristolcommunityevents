import os

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
DEFAULT_ADMIN_NAME = os.getenv("DEFAULT_ADMIN_NAME", "Administrator")
DEFAULT_ADMIN_EMAIL = os.getenv(
    "DEFAULT_ADMIN_EMAIL",
    ADMIN_USERNAME if "@" in ADMIN_USERNAME else "admin@bristol-events.local",
)
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", ADMIN_PASSWORD)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "Uwe#@#98714UK"),
    "database": os.getenv("DB_NAME", "bristol_community_events_db"),
}
