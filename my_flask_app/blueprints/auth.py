from flask import Blueprint

from ._support import register_url_rules

bp = Blueprint("auth", __name__)

ROUTES = [
    ("/login", "login", {"methods": ["GET", "POST"]}),
    ("/register", "register", {"methods": ["GET", "POST"]}),
    ("/logout", "logout", {}),
    ("/reset-password/<token>", "reset_password", {"methods": ["GET", "POST"]}),
]


@bp.record_once
def register_auth_routes(state):
    register_url_rules(state, ROUTES)

