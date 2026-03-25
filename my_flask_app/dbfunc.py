from __future__ import annotations

import mysql.connector

from config import DB_CONFIG


def get_db_connection() -> mysql.connector.connection.MySQLConnection:
    return mysql.connector.connect(**DB_CONFIG, connect_timeout=10, use_pure=True)
