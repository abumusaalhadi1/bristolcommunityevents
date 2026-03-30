from flask import Blueprint

from ._support import register_error_handlers

bp = Blueprint("errors", __name__)

HANDLERS = [
    (403, "forbidden"),
    (404, "page_not_found"),
    (401, "unauthorized"),
    (500, "internal_error"),
]


@bp.record_once
def register_error_routes(state):
    register_error_handlers(state, HANDLERS)

