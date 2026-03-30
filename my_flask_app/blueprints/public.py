from flask import Blueprint

from ._support import register_url_rules

bp = Blueprint("public", __name__)

ROUTES = [
    ("/view", "view_events", {}),
    ("/", "home", {}),
    ("/subscribe", "subscribe", {"methods": ["POST"]}),
    ("/reviews", "submit_review", {"methods": ["GET", "POST"]}),
    ("/events", "events", {}),
    ("/venues", "venues_page", {}),
    ("/venues/<int:venue_id>", "venue_detail", {}),
    ("/book-tickets", "book_tickets", {"methods": ["GET", "POST"]}),
    ("/event/<int:event_id>", "event_detail", {}),
    ("/book/<int:event_id>", "book", {"methods": ["GET", "POST"]}),
    ("/bookings", "bookings_list", {}),
    ("/bookings/<int:booking_id>/receipt", "booking_receipt", {}),
    ("/bookings/<int:booking_id>/receipt/download", "download_booking_receipt", {}),
    ("/bookings/<int:booking_id>/cancel", "cancel_booking", {"methods": ["POST"]}),
    ("/contact", "contact", {"methods": ["GET", "POST"]}),
]


@bp.record_once
def register_public_routes(state):
    register_url_rules(state, ROUTES)

