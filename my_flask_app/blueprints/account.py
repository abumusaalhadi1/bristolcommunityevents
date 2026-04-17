from flask import Blueprint

from ._support import register_url_rules

bp = Blueprint("account", __name__)

ROUTES = [
    ("/my-bookings", "my_bookings", {}),
    ("/account", "account", {"methods": ["GET", "POST"]}),
    ("/account/contact-messages/<int:message_id>/delete", "account_delete_contact_message", {"methods": ["POST"]}),
    ("/account/reviews/<int:review_id>/edit", "account_edit_review", {"methods": ["GET", "POST"]}),
    ("/account/reviews/<int:review_id>/delete", "account_delete_review", {"methods": ["POST"]}),
    ("/waitlist/<int:waitlist_id>/accept", "accept_waitlist_offer", {}),
    ("/waitlist/<int:waitlist_id>/reject", "reject_waitlist_offer", {"methods": ["POST"]}),
    ("/edit_booking/<int:booking_id>", "edit_booking", {"methods": ["GET", "POST"]}),
    ("/delete_booking/<int:booking_id>", "delete_booking", {"methods": ["POST"]}),
]


@bp.record_once
def register_account_routes(state):
    register_url_rules(state, ROUTES)
