from flask import Blueprint

from ._support import register_url_rules

bp = Blueprint("admin", __name__)

ROUTES = [
    ("/admin/events", "admin_events", {}),
    ("/add", "add_event", {"methods": ["GET", "POST"]}),
    ("/update/<int:event_id>", "update_event", {"methods": ["GET", "POST"]}),
    ("/delete/<int:event_id>", "delete_event", {"methods": ["GET", "POST"]}),
    ("/admin/login", "admin_login", {"methods": ["GET", "POST"]}),
    ("/admin/logout", "admin_logout", {}),
    ("/admin", "admin_dashboard", {}),
    ("/admin/subscribers", "admin_subscribers", {}),
    ("/admin/waitlist", "admin_waitlist", {}),
    ("/admin/waitlist/trigger-offers", "admin_trigger_waitlist_offers", {"methods": ["POST"]}),
    ("/admin/reports", "admin_reports", {}),
    ("/admin/users/invite", "admin_send_user_role_invite_email", {"methods": ["POST"]}),
    ("/admin/users/invite/<int:invitation_id>/resend", "admin_resend_user_role_invite_email", {"methods": ["POST"]}),
    ("/admin/users/invite/<int:invitation_id>/revoke", "admin_revoke_user_role_invite", {"methods": ["POST"]}),
    ("/admin/reports/export.csv", "admin_reports_export_csv", {}),
    ("/admin/reports/export.pdf", "admin_reports_export_pdf", {}),
    ("/admin/venues", "admin_venues", {}),
    ("/admin/venues/add", "add_venue", {"methods": ["GET", "POST"]}),
    ("/admin/venues/<int:venue_id>/edit", "update_venue", {"methods": ["GET", "POST"]}),
    ("/admin/venues/<int:venue_id>/delete", "delete_venue", {"methods": ["GET", "POST"]}),
    ("/admin/bookings", "admin_bookings", {}),
    ("/admin/receipts", "admin_receipts", {}),
    ("/admin/bookings/<int:booking_id>/edit", "admin_edit_booking", {"methods": ["GET", "POST"]}),
    ("/admin/bookings/<int:booking_id>/delete", "admin_delete_booking", {"methods": ["POST"]}),
    ("/admin/users", "admin_users", {}),
    ("/admin/users/<int:user_id>/delete", "admin_delete_user", {"methods": ["POST"]}),
    ("/admin/users/<int:user_id>/password", "admin_reset_user_password", {"methods": ["GET", "POST"]}),
    ("/admin/users/<int:user_id>/password/email", "admin_send_user_password_reset_email", {"methods": ["POST"]}),
    ("/admin/reviews", "admin_reviews", {}),
    ("/admin/contact-messages", "admin_contact_messages", {}),
    ("/admin/contact-messages/<int:message_id>/reply", "admin_reply_contact_message", {"methods": ["GET", "POST"]}),
    ("/admin/contact-messages/<int:message_id>/delete", "admin_delete_contact_message", {"methods": ["POST"]}),
    ("/admin/reviews/<int:review_id>/approve", "admin_approve_review", {"methods": ["POST"]}),
    ("/admin/reviews/<int:review_id>/reject", "admin_reject_review", {"methods": ["POST"]}),
    ("/admin/reviews/<int:review_id>/delete", "admin_delete_review", {"methods": ["POST"]}),
    ("/admin/events/<int:event_id>/reduce-price", "reduce_event_price", {"methods": ["POST"]}),
]


@bp.record_once
def register_admin_routes(state):
    register_url_rules(state, ROUTES)
