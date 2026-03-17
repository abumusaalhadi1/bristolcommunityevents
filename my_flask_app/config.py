import os

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "Amah#@#98UK"),
    "database": os.getenv("DB_NAME", "uwe_events_db"),
}

