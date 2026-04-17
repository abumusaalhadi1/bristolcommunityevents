"""Flask application for Bristol Community Events.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from collections import Counter, defaultdict
import csv
import io
from functools import wraps
from html import escape as html_escape
from email.message import EmailMessage
from pathlib import Path
import hashlib
import re
import secrets
import smtplib
import ssl
import textwrap
import colorsys
from uuid import uuid4

import mysql.connector
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import (
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_NAME,
    DEFAULT_ADMIN_PASSWORD,
    MAIL_DEFAULT_SENDER,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_SUPPRESS_SEND,
    MAIL_USE_SSL,
    MAIL_USE_TLS,
    MAIL_USERNAME,
    PUBLIC_APP_URL,
    SECRET_KEY,
)
from dbfunc import get_db_connection
from receipt import (
    STUDENT_ID_DISCLAIMER,
    booking_receipt_reference,
    build_booking_receipt_pdf as _build_booking_receipt_pdf,
    build_admin_reports_pdf,
)
from seed_data import DEFAULT_CATEGORIES, DEFAULT_EVENTS, DEFAULT_REVIEWS, DEFAULT_VENUES

app = Flask(__name__)
app.secret_key = SECRET_KEY
VENUE_IMAGE_DIR = Path(__file__).resolve().parent / "static" / "images" / "venue"
EVENT_IMAGE_UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "images" / "event" / "uploads"
ALLOWED_EVENT_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}

# Core app constants and seed data are kept together for easier navigation.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

ROLE_ADMIN = "admin"
ROLE_USER = "user"
BOOKING_STATUSES = ("Pending", "Confirmed", "Cancelled")
CONTACT_MESSAGE_STATUSES = ("New", "Replied")
PAYMENT_METHODS = {"paypal", "card", "bank"}
PAYMENT_METHOD_LABELS = {
    "paypal": "PayPal",
    "card": "Credit/Debit Card",
    "bank": "Bank Transfer",
}
PENDING_BOOKING_SESSION_KEY = "pending_booking"
BOOKING_ADVANCE_WINDOW_DAYS = 60
WAITLIST_OFFER_HOLD_DAYS = 2
EVENT_PRICE_REDUCTION_THRESHOLD = Decimal("0.25")
EVENT_PRICE_REDUCTION_BOOKING_RATIO = Decimal("0.50")
EVENT_PRICE_REDUCTION_LOOKBACK_DAYS = 10
EVENT_COST_MARGIN = Decimal("0.15")
REFUND_WINDOW_HOURS = 72
REFUND_PROCESSING_WORKING_DAYS = 2
ACTIVE_BOOKING_CONDITION = "COALESCE(status, 'Confirmed') <> 'Cancelled'"

WAITLIST_STATUS_WAITING = "Waiting"
WAITLIST_STATUS_OFFERED = "Offered"
WAITLIST_STATUS_CONVERTED = "Converted"
WAITLIST_STATUS_EXPIRED = "Expired"
WAITLIST_STATUS_CANCELLED = "Cancelled"
WAITLIST_STATUSES = (
    WAITLIST_STATUS_WAITING,
    WAITLIST_STATUS_OFFERED,
    WAITLIST_STATUS_CONVERTED,
    WAITLIST_STATUS_EXPIRED,
    WAITLIST_STATUS_CANCELLED,
)

CARD_NUMBER_RE = re.compile(r"^\d{13,19}$")
CARD_EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])\/(\d{2}|\d{4})$")
CVV_RE = re.compile(r"^\d{3,4}$")
SORT_CODE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")

REVIEW_STATUS_PENDING = "Pending"
REVIEW_STATUS_APPROVED = "Approved"
REVIEW_STATUS_REJECTED = "Rejected"
REVIEW_STATUSES = (
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
)
PASSWORD_RESET_TOKEN_TTL_HOURS = 24
ROLE_INVITE_TOKEN_TTL_HOURS = 72
PASSWORD_VERSION_SESSION_KEY = "auth_password_changed_at"

ALLOWED_USER_ROLES = {ROLE_USER, ROLE_ADMIN}

_db_initialized = False


# Input parsing and validation helpers.
def parse_ticket_count(value):
    try:
        tickets = int(value)
    except (TypeError, ValueError):
        return None

    if 1 <= tickets <= 10:
        return tickets
    return None


def parse_booking_days(value, max_days=1):
    try:
        booking_days = int(value)
    except (TypeError, ValueError):
        return None

    max_days = max(1, int(max_days or 1))
    if 1 <= booking_days <= max_days:
        return booking_days
    return None


def parse_positive_int(value, default=1):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return number if number > 0 else default


def parse_event_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_price(value):
    if value is None:
        return Decimal("0.00")

    value = str(value).strip()
    if value == "":
        return Decimal("0.00")

    try:
        price = Decimal(value)
    except (TypeError, InvalidOperation):
        return None

    if price < 0:
        return None

    return price.quantize(Decimal("0.01"))


def parse_capacity(value):
    value = (value or "").strip()
    if value == "":
        return None

    try:
        capacity = int(value)
    except (TypeError, ValueError):
        return None

    if capacity < 1 or capacity > 10000:
        return None

    return capacity


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or ""))


def normalize_payment_method(value: str) -> str:
    payment_method = (value or "").strip().lower()
    return payment_method if payment_method in PAYMENT_METHODS else ""


def normalize_user_role(value: str) -> str:
    role = (value or "").strip().lower()
    return role if role in ALLOWED_USER_ROLES else ROLE_USER


def payment_method_label(payment_method: str) -> str:
    return PAYMENT_METHOD_LABELS.get(payment_method, "Payment method")


def normalize_card_number(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_bank_reference(value: str) -> str:
    normalized = re.sub(r"\s+", "", (value or "").strip().upper())
    if normalized.isdigit() and len(normalized) == 6:
        return f"{normalized[:2]}-{normalized[2:4]}-{normalized[4:]}"
    return normalized


def is_valid_card_expiry(value: str) -> bool:
    value = (value or "").strip()
    match = CARD_EXPIRY_RE.match(value)
    if not match:
        return False

    month = int(match.group(1))
    year = int(match.group(2))
    if year < 100:
        year += 2000

    if month == 12:
        expiry_boundary = datetime(year + 1, 1, 1)
    else:
        expiry_boundary = datetime(year, month + 1, 1)

    return current_datetime() < expiry_boundary


def build_payment_source_summary(payment_method: str, form_data) -> str:
    if payment_method == "paypal":
        email = (form_data.get("paypal_email") or "").strip().lower()
        return f"PayPal account {email}" if email else "PayPal account"

    if payment_method == "card":
        card_number = normalize_card_number(form_data.get("card_number"))
        card_tail = card_number[-4:] if card_number else ""
        name_on_card = (form_data.get("card_name") or "").strip()
        summary = "Card"
        if card_tail:
            summary += f" ending {card_tail}"
        if name_on_card:
            summary += f" in the name of {name_on_card}"
        return summary

    if payment_method == "bank":
        account_number = normalize_card_number(form_data.get("bank_account_number"))
        account_tail = account_number[-4:] if account_number else ""
        holder_name = (form_data.get("bank_holder") or "").strip()
        bank_reference = normalize_bank_reference(form_data.get("bank_sort_code_or_iban"))
        summary = "Bank transfer"
        if account_tail:
            summary += f" account ending {account_tail}"
        if holder_name:
            summary += f" in the name of {holder_name}"
        if bank_reference:
            summary += f" ({bank_reference})"
        return summary

    return payment_method_label(payment_method)


def validate_payment_details(payment_method: str, form_data):
    payment_method = normalize_payment_method(payment_method)
    field_errors = {}
    cleaned_values = {}

    if payment_method == "card":
        name_on_card = (form_data.get("card_name") or "").strip()
        card_number = normalize_card_number(form_data.get("card_number"))
        expiry_date = (form_data.get("card_expiry") or "").strip()
        cvv = (form_data.get("card_cvv") or "").strip()

        if not name_on_card:
            field_errors["card_name"] = "Name on card is required."
        if not CARD_NUMBER_RE.fullmatch(card_number):
            field_errors["card_number"] = "Enter a card number with 13 to 19 digits."
        if not is_valid_card_expiry(expiry_date):
            field_errors["card_expiry"] = "Enter a valid expiry date in MM/YY or MM/YYYY format."
        if not CVV_RE.fullmatch(cvv):
            field_errors["card_cvv"] = "Enter a 3 or 4 digit CVV."

        cleaned_values = {
            "card_name": name_on_card,
            "card_number": card_number,
            "card_expiry": expiry_date,
            "card_cvv": cvv,
        }
    elif payment_method == "paypal":
        paypal_email = (form_data.get("paypal_email") or "").strip().lower()
        paypal_password = (form_data.get("paypal_password") or "").strip()

        if not is_valid_email(paypal_email):
            field_errors["paypal_email"] = "Enter a valid PayPal email address."
        if len(paypal_password) < 6:
            field_errors["paypal_password"] = "Enter your PayPal password for this booking."

        cleaned_values = {
            "paypal_email": paypal_email,
            "paypal_password": paypal_password,
        }
    elif payment_method == "bank":
        bank_holder = (form_data.get("bank_holder") or "").strip()
        bank_account_number = normalize_card_number(form_data.get("bank_account_number"))
        bank_reference = normalize_bank_reference(form_data.get("bank_sort_code_or_iban"))

        if not bank_holder:
            field_errors["bank_holder"] = "Account holder name is required."
        if not 6 <= len(bank_account_number) <= 12:
            field_errors["bank_account_number"] = "Enter a bank account number with 6 to 12 digits."
        if not (SORT_CODE_RE.fullmatch(bank_reference) or IBAN_RE.fullmatch(bank_reference)):
            field_errors["bank_sort_code_or_iban"] = "Enter a valid sort code (12-34-56) or IBAN."

        cleaned_values = {
            "bank_holder": bank_holder,
            "bank_account_number": bank_account_number,
            "bank_sort_code_or_iban": bank_reference,
        }
    else:
        field_errors["payment_method"] = "Please choose a valid payment method."

    return field_errors, cleaned_values


def refund_deadline_for_event(event_date):
    if not event_date:
        return None
    return event_date - timedelta(days=40)


def refund_is_allowed(event_date):
    refund_deadline = refund_deadline_for_event(event_date)
    if refund_deadline is None:
        return False, None
    return current_date() <= refund_deadline, refund_deadline


def to_money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (TypeError, InvalidOperation):
        return Decimal("0.00")


def event_cost_from_price(price) -> Decimal:
    price_amount = to_money(price)
    if price_amount <= 0:
        return Decimal("0.00")
    return (price_amount * EVENT_COST_MARGIN).quantize(Decimal("0.01"))


def fetch_event_mix_counts(cursor, start_date, end_date):
    cursor.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN booking_counts.total_bookings IS NULL THEN 1 ELSE 0 END), 0) AS no_booking_events_count,
            COALESCE(SUM(CASE WHEN booking_counts.active_bookings > 0 THEN 1 ELSE 0 END), 0) AS active_booked_events_count,
            COALESCE(SUM(
                CASE
                    WHEN booking_counts.total_bookings > 0
                         AND booking_counts.active_bookings = 0
                    THEN 1 ELSE 0
                END
            ), 0) AS cancelled_only_events_count
        FROM events e
        LEFT JOIN (
            SELECT b.event_id,
                   COUNT(*) AS total_bookings,
                   SUM(CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN 1 ELSE 0 END) AS active_bookings
            FROM bookings b
            GROUP BY b.event_id
        ) booking_counts ON booking_counts.event_id = e.event_id
        WHERE e.event_date BETWEEN %s AND %s
        """,
        (start_date, end_date),
    )
    return cursor.fetchone() or {}


def parse_report_date(value):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def build_report_period_context(chart_period_raw, selected_year, start_raw="", end_raw=""):
    today = current_date()
    period_key = (chart_period_raw or "").strip().lower()

    if period_key not in {"weekly", "monthly", "yearly", "custom"}:
        period_key = "yearly"

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    next_month_start = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month_start - timedelta(days=1)
    year_start = today.replace(year=selected_year, month=1, day=1)
    year_end = today.replace(year=selected_year, month=12, day=31)

    if period_key == "weekly":
        return {
            "period_key": period_key,
            "start_date": week_start,
            "end_date": week_end,
            "title": "Weekly Analytics",
            "note": f"Bookings and revenue between {week_start.strftime('%b %d, %Y')} and {week_end.strftime('%b %d, %Y')}.",
        }

    if period_key == "monthly":
        return {
            "period_key": period_key,
            "start_date": month_start,
            "end_date": month_end,
            "title": "Monthly Analytics",
            "note": f"Bookings and revenue between {month_start.strftime('%b %d, %Y')} and {month_end.strftime('%b %d, %Y')}.",
        }

    if period_key == "custom":
        start_date = parse_report_date(start_raw) or month_start
        end_date = parse_report_date(end_raw) or today
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        return {
            "period_key": period_key,
            "start_date": start_date,
            "end_date": end_date,
            "title": "Custom Date Range",
            "note": f"Bookings and revenue between {start_date.strftime('%b %d, %Y')} and {end_date.strftime('%b %d, %Y')}.",
        }

    return {
        "period_key": "yearly",
        "start_date": year_start,
        "end_date": year_end,
        "title": f"Yearly Analytics - {selected_year}",
        "note": f"Bookings and revenue in {selected_year}.",
    }


def previous_date_range(start_date, end_date):
    if not start_date or not end_date:
        return None, None
    span_days = max((end_date - start_date).days + 1, 1)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span_days - 1)
    return previous_start, previous_end


def report_bucket_key(period_key: str, booked_at: datetime, start_date, end_date):
    span_days = max((end_date - start_date).days + 1, 1)
    if period_key == "yearly":
        return booked_at.strftime("%Y-%m")
    if period_key == "custom" and span_days > 90:
        return booked_at.strftime("%Y-%m")
    return booked_at.strftime("%Y-%m-%d")


def report_bucket_label(period_key: str, bucket_key: str):
    if period_key == "yearly" or len(bucket_key) == 7:
        try:
            return datetime.strptime(bucket_key, "%Y-%m").strftime("%b %Y")
        except ValueError:
            return bucket_key
    try:
        return datetime.strptime(bucket_key, "%Y-%m-%d").strftime("%b %d")
    except ValueError:
        return bucket_key


def fetch_report_booking_rows(cursor, start_date, end_date):
    booked_at_sql = booking_booked_at_sql("b")
    cursor.execute(
        f"""
        SELECT b.booking_id, b.user_id, b.event_id, COALESCE(b.status, 'Confirmed') AS status,
               b.tickets, b.booking_days, b.subtotal_amount, b.discount_applied,
               b.cancellation_charge, b.refund_amount, b.created_at, b.booking_date,
               {booked_at_sql} AS booked_at,
               e.event_name, e.event_date, e.event_end_date, e.price, e.event_cost,
               c.category_name,
               u.full_name
        FROM bookings b
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        LEFT JOIN users u ON b.user_id = u.user_id
        WHERE DATE({booked_at_sql}) BETWEEN %s AND %s
        ORDER BY booked_at ASC, b.booking_id ASC
        """,
        (start_date, end_date),
    )
    return cursor.fetchall()


def fetch_report_waitlist_rows(cursor, start_date, end_date):
    cursor.execute(
        """
        SELECT w.waitlist_id, w.event_id, w.user_id, w.requested_tickets, w.booking_days,
               w.status, w.created_at, w.updated_at,
               e.event_name, e.event_date, e.event_end_date,
               c.category_name
        FROM event_waitlist w
        JOIN events e ON w.event_id = e.event_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        WHERE e.event_date BETWEEN %s AND %s
        ORDER BY w.created_at DESC, w.waitlist_id DESC
        """,
        (start_date, end_date),
    )
    return cursor.fetchall()


def build_report_metrics(rows):
    metrics = {
        "bookings_count": 0,
        "active_bookings_count": 0,
        "cancelled_bookings_count": 0,
        "tickets_sold": 0,
        "revenue_total": Decimal("0.00"),
        "cancellation_charge_total": Decimal("0.00"),
        "refund_total": Decimal("0.00"),
    }
    customer_counts = Counter()

    for row in rows:
        metrics["bookings_count"] += 1
        status = (row.get("status") or "").strip()
        is_cancelled = status == "Cancelled"
        if is_cancelled:
            metrics["cancelled_bookings_count"] += 1
            metrics["cancellation_charge_total"] += to_money(row.get("cancellation_charge"))
            metrics["refund_total"] += to_money(row.get("refund_amount"))
        else:
            metrics["active_bookings_count"] += 1
            metrics["tickets_sold"] += int(row.get("tickets") or 0)
            subtotal = to_money(row.get("subtotal_amount"))
            discount = to_money(row.get("discount_applied"))
            metrics["revenue_total"] += max(subtotal - discount, Decimal("0.00"))
            user_id = row.get("user_id")
            if user_id is not None:
                customer_counts[user_id] += 1

    unique_customers = len(customer_counts)
    repeat_bookers = sum(1 for count in customer_counts.values() if count > 1)
    booking_completion_rate = (
        round((metrics["active_bookings_count"] / metrics["bookings_count"]) * 100, 1)
        if metrics["bookings_count"]
        else 0.0
    )
    cancellation_rate = (
        round((metrics["cancelled_bookings_count"] / metrics["bookings_count"]) * 100, 1)
        if metrics["bookings_count"]
        else 0.0
    )
    avg_spend = (
        (metrics["revenue_total"] / unique_customers).quantize(Decimal("0.01"))
        if unique_customers
        else Decimal("0.00")
    )

    metrics.update(
        {
            "unique_customers": unique_customers,
            "repeat_bookers": repeat_bookers,
            "booking_completion_rate": booking_completion_rate,
            "cancellation_rate": cancellation_rate,
            "avg_spend_per_customer": avg_spend,
        }
    )
    return metrics, customer_counts


def build_report_timeseries(rows, start_date, end_date, period_key):
    buckets = {}
    for row in rows:
        booked_at = row.get("booked_at") or row.get("created_at")
        if not booked_at:
            continue
        bucket_key = report_bucket_key(period_key, booked_at, start_date, end_date)
        entry = buckets.setdefault(bucket_key, Decimal("0.00"))
        status = (row.get("status") or "").strip()
        if status != "Cancelled":
            subtotal = to_money(row.get("subtotal_amount"))
            discount = to_money(row.get("discount_applied"))
            buckets[bucket_key] = entry + max(subtotal - discount, Decimal("0.00"))

    if not buckets:
        return [], []

    sorted_keys = sorted(buckets.keys())
    labels = [report_bucket_label(period_key, key) for key in sorted_keys]
    values = [float(buckets[key]) for key in sorted_keys]
    return labels, values


def build_report_top_events(rows, limit=5):
    event_totals = defaultdict(lambda: {"label": "", "value": Decimal("0.00")})
    for row in rows:
        if (row.get("status") or "").strip() == "Cancelled":
            continue
        event_id = row.get("event_id")
        if event_id is None:
            continue
        subtotal = to_money(row.get("subtotal_amount"))
        discount = to_money(row.get("discount_applied"))
        event_totals[event_id]["label"] = row.get("event_name") or "Event"
        event_totals[event_id]["value"] += max(subtotal - discount, Decimal("0.00"))

    top_events = sorted(event_totals.values(), key=lambda item: item["value"], reverse=True)[:limit]
    return [item["label"] for item in top_events], [float(item["value"]) for item in top_events]


def build_report_category_breakdown(rows):
    category_totals = defaultdict(Decimal)
    for row in rows:
        if (row.get("status") or "").strip() == "Cancelled":
            continue
        category = row.get("category_name") or "Uncategorised"
        category_totals[category] += Decimal(str(int(row.get("tickets") or 0)))

    sorted_items = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
    return [label for label, _ in sorted_items], [float(value) for _, value in sorted_items]


def build_report_customer_rows(rows, limit=5):
    customer_totals = defaultdict(lambda: {"name": "", "email": "", "bookings": 0, "spend": Decimal("0.00")})
    for row in rows:
        if (row.get("status") or "").strip() == "Cancelled":
            continue
        user_id = row.get("user_id")
        if user_id is None:
            continue
        subtotal = to_money(row.get("subtotal_amount"))
        discount = to_money(row.get("discount_applied"))
        customer = customer_totals[user_id]
        customer["name"] = row.get("full_name") or customer["name"] or "Customer"
        customer["bookings"] += 1
        customer["spend"] += max(subtotal - discount, Decimal("0.00"))

    sorted_customers = sorted(customer_totals.values(), key=lambda item: item["spend"], reverse=True)[:limit]
    for customer in sorted_customers:
        customer["spend"] = customer["spend"].quantize(Decimal("0.01"))
    return sorted_customers


def build_report_waitlist_summary(waitlist_rows, event_rows):
    event_counts = defaultdict(lambda: {"label": "", "requests": 0, "tickets": 0})
    total_requests = 0
    total_tickets = 0

    for row in waitlist_rows:
        event_id = row.get("event_id")
        if event_id is None:
            continue
        requests = 1
        tickets = int(row.get("requested_tickets") or 0)
        event = event_counts[event_id]
        event["label"] = row.get("event_name") or "Event"
        event["requests"] += requests
        event["tickets"] += tickets
        total_requests += requests
        total_tickets += tickets

    top_events = sorted(event_counts.values(), key=lambda item: (item["requests"], item["tickets"]), reverse=True)
    return {
        "total_requests": total_requests,
        "total_tickets": total_tickets,
        "top_events": top_events[:5],
    }


def build_report_cancellation_summary(rows):
    cancelled_rows = [row for row in rows if (row.get("status") or "").strip() == "Cancelled"]
    total_rows = len(rows)
    cancelled_count = len(cancelled_rows)
    cancellation_rate = round((cancelled_count / total_rows) * 100, 1) if total_rows else 0.0
    lost_revenue = sum((to_money(row.get("subtotal_amount")) - to_money(row.get("discount_applied"))) for row in cancelled_rows)
    charge_collected = sum(to_money(row.get("cancellation_charge")) for row in cancelled_rows)

    event_counts = defaultdict(lambda: {"label": "", "count": 0, "lost": Decimal("0.00")})
    for row in cancelled_rows:
        event_id = row.get("event_id")
        if event_id is None:
            continue
        item = event_counts[event_id]
        item["label"] = row.get("event_name") or "Event"
        item["count"] += 1
        item["lost"] += max(to_money(row.get("subtotal_amount")) - to_money(row.get("discount_applied")), Decimal("0.00"))

    top_events = sorted(event_counts.values(), key=lambda item: (item["count"], item["lost"]), reverse=True)[:5]
    return {
        "cancelled_count": cancelled_count,
        "cancellation_rate": cancellation_rate,
        "lost_revenue": lost_revenue.quantize(Decimal("0.01")),
        "charge_collected": charge_collected.quantize(Decimal("0.01")),
        "top_events": top_events,
    }


def event_duration_days(event) -> int:
    if not event:
        return 1

    event_start = event.get("event_date")
    event_end = event.get("event_end_date") or event_start

    if not event_start or not event_end or event_end < event_start:
        return 1

    return max((event_end - event_start).days + 1, 1)


def event_date_range_label(event) -> str:
    if not event or not event.get("event_date"):
        return ""

    event_start = event["event_date"]
    event_end = event.get("event_end_date") or event_start

    if event_end and event_end != event_start:
        return f"{event_start.strftime('%B %d, %Y')} - {event_end.strftime('%B %d, %Y')}"

    return event_start.strftime("%B %d, %Y")


def event_booking_open_date(event):
    event_start = event.get("event_date") if event else None
    if not event_start:
        return None
    return event_start - timedelta(days=BOOKING_ADVANCE_WINDOW_DAYS)


def event_booking_close_date(event):
    if not event:
        return None
    return event.get("event_end_date") or event.get("event_date")


def is_event_bookable(event, reference_date=None) -> bool:
    if not event:
        return False

    reference_date = reference_date or current_date()
    event_start = event.get("event_date")
    event_close = event_booking_close_date(event)
    if not event_start or not event_close:
        return False

    open_date = event_booking_open_date(event)
    return bool(open_date and open_date <= reference_date <= event_close)


def days_before_event(event_date, reference_date=None):
    if not event_date:
        return None

    reference_date = reference_date or current_date()
    return (event_date - reference_date).days


def advance_booking_discount_rate(days_before: int | None):
    if days_before is None:
        return Decimal("0.00")
    if days_before > BOOKING_ADVANCE_WINDOW_DAYS:
        return Decimal("0.00")
    if 50 <= days_before <= 60:
        return Decimal("0.20")
    if 35 <= days_before < 50:
        return Decimal("0.15")
    if 25 <= days_before < 35:
        return Decimal("0.10")
    if 15 <= days_before < 25:
        return Decimal("0.05")
    return Decimal("0.00")


def calculate_booking_breakdown(
    price,
    tickets: int,
    is_student: bool,
    *,
    booking_days: int = 1,
    event_duration: int = 1,
    event_date=None,
    booked_at=None,
):
    event_total_price = to_money(price)
    tickets = max(int(tickets or 0), 1)
    booking_days = max(int(booking_days or 1), 1)
    event_duration = max(int(event_duration or 1), 1)
    booked_at = booked_at or current_datetime()

    per_day_price = (event_total_price / Decimal(event_duration)).quantize(Decimal("0.01"))
    if event_duration == 1:
        per_day_price = event_total_price

    base_subtotal = (per_day_price * tickets * booking_days).quantize(Decimal("0.01"))
    student_discount = Decimal("0.00")
    if is_student:
        student_discount = (base_subtotal * Decimal("0.10")).quantize(Decimal("0.01"))

    advance_days = days_before_event(event_date, booked_at.date()) if event_date else None
    advance_rate = advance_booking_discount_rate(advance_days)
    advance_discount = (base_subtotal * advance_rate).quantize(Decimal("0.01"))
    discount_total = (student_discount + advance_discount).quantize(Decimal("0.01"))
    total = (base_subtotal - discount_total).quantize(Decimal("0.01"))
    if total < 0:
        total = Decimal("0.00")

    return {
        "per_day_price": per_day_price,
        "base_subtotal": base_subtotal,
        "student_discount": student_discount,
        "advance_discount": advance_discount,
        "discount_total": discount_total,
        "total": total,
        "advance_discount_rate": advance_rate,
        "advance_days": advance_days,
        "booking_days": booking_days,
        "event_duration": event_duration,
    }


def compute_booking_amounts(
    price,
    tickets: int,
    is_student: bool,
    *,
    booking_days: int = 1,
    event_duration: int = 1,
    event_date=None,
    booked_at=None,
):
    breakdown = calculate_booking_breakdown(
        price,
        tickets,
        is_student,
        booking_days=booking_days,
        event_duration=event_duration,
        event_date=event_date,
        booked_at=booked_at,
    )
    return breakdown["base_subtotal"], breakdown["discount_total"], breakdown["total"]


def cancellation_charge_rate(days_before: int | None):
    if days_before is None:
        return Decimal("1.00")
    if days_before >= 40:
        return Decimal("0.00")
    if 25 <= days_before < 40:
        return Decimal("0.40")
    return Decimal("1.00")


def calculate_cancellation_charge(total_amount, event_date, cancelled_at=None):
    cancelled_at = cancelled_at or current_datetime()
    days_before = days_before_event(event_date, cancelled_at.date()) if event_date else None
    charge_rate = cancellation_charge_rate(days_before)
    total_amount = to_money(total_amount)
    charge = (total_amount * charge_rate).quantize(Decimal("0.01"))
    refund = (total_amount - charge).quantize(Decimal("0.01"))
    if refund < 0:
        refund = Decimal("0.00")
    return {
        "days_before": days_before,
        "charge_rate": charge_rate,
        "cancellation_charge": charge,
        "refund_amount": refund,
    }


def is_safe_next_url(value: str) -> bool:
    return bool(value) and value.startswith("/") and not value.startswith("//")


def get_safe_next_url() -> str:
    candidate = request.args.get("next") or request.form.get("next") or ""
    return candidate if is_safe_next_url(candidate) else ""


def current_request_path() -> str:
    full_path = request.full_path if request.query_string else request.path
    return full_path[:-1] if full_path.endswith("?") else full_path


def current_datetime():
    return datetime.now()


def current_date():
    return current_datetime().date()


def build_public_url(endpoint: str, **values) -> str:
    path = url_for(endpoint, **values)
    if PUBLIC_APP_URL:
        return f"{PUBLIC_APP_URL.rstrip('/')}{path}"
    return url_for(endpoint, _external=True, **values)


def serialize_password_version(value):
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    if value is None:
        return ""
    return str(value)


def clear_auth_session():
    for key in ("user_id", "user_role", "user_name", PASSWORD_VERSION_SESSION_KEY):
        session.pop(key, None)


def password_reset_token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def role_invitation_token_hash(token: str) -> str:
    return hashlib.sha256(("invite:" + (token or "")).encode("utf-8")).hexdigest()


def build_password_reset_email(user, reset_url: str, _expires_at) -> tuple[str, str]:
    user_name = (user.get("full_name") or "there").strip() or "there"
    body = textwrap.dedent(
        f"""\
        Dear {user_name},

        A password reset request has been initiated for your account on Bristol Community Events.

        To set a new password, please click the secure link below:

        {reset_url}

        This link will expire in {PASSWORD_RESET_TOKEN_TTL_HOURS} hours for security reasons.

        If you did not request this password reset, please ignore this email. Your account will remain unchanged.

        For any concerns, please contact our support team.

        Kind regards,
        Bristol Community Events Team
        """
    ).strip()
    subject = "Reset your Bristol Community Events password"
    return subject, body


def build_access_invitation_email(user, invite_url: str, _expires_at, role: str | None = None) -> tuple[str, str]:
    user_name = (user.get("full_name") or "there").strip() or "there"
    role = (role or user.get("role") or ROLE_USER).strip().lower()
    role_label = "administrator" if role == ROLE_ADMIN else "member"
    body = textwrap.dedent(
        f"""\
        Dear {user_name},

        You have been invited to join Bristol Community Events as a {role_label}.

        To activate your access, please click the secure link below and set your password:

        {invite_url}

        This link will expire in {ROLE_INVITE_TOKEN_TTL_HOURS} hours for security reasons.

        If you were not expecting this invitation, you can safely ignore this email.

        Kind regards,
        Bristol Community Events Team
        """
    ).strip()
    subject = "Your Bristol Community Events access invitation"
    return subject, body


def send_email_message(subject: str, body: str, recipient_email: str):
    if MAIL_SUPPRESS_SEND:
        app.logger.info("MAIL_SUPPRESS_SEND is enabled; skipping email to %s", recipient_email)
        app.logger.info("Email subject: %s", subject)
        app.logger.info("Email body:\n%s", body)
        return

    if not MAIL_SERVER:
        raise RuntimeError(
            "Email is not configured. Set MAIL_SERVER and the related MAIL_* values before sending reset links."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = MAIL_DEFAULT_SENDER or MAIL_USERNAME
    message["To"] = recipient_email
    message.set_content(body)

    context = ssl.create_default_context()
    if MAIL_USE_SSL:
        with smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=10, context=context) as smtp:
            smtp.ehlo()
            if MAIL_USERNAME:
                smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
            smtp.send_message(message)
        return

    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as smtp:
        smtp.ehlo()
        if MAIL_USE_TLS:
            smtp.starttls(context=context)
            smtp.ehlo()
        if MAIL_USERNAME:
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.send_message(message)


def create_password_reset_request(cursor, user_id: int, requested_by: int | None):
    raw_token = secrets.token_urlsafe(32)
    token_hash = password_reset_token_hash(raw_token)
    now = current_datetime()
    expires_at = now + timedelta(hours=PASSWORD_RESET_TOKEN_TTL_HOURS)

    cursor.execute(
        """
        INSERT INTO password_reset_tokens (
            user_id, token_hash, requested_by, created_at, expires_at
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, token_hash, requested_by, now, expires_at),
    )
    return raw_token, expires_at, token_hash


def fetch_password_reset_request(cursor, token: str):
    token_hash = password_reset_token_hash(token)
    cursor.execute(
        """
        SELECT r.reset_token_id, r.user_id, r.token_hash, r.created_at, r.expires_at, r.used_at,
               r.requested_by, u.full_name, u.email
        FROM password_reset_tokens r
        JOIN users u ON u.user_id = r.user_id
        WHERE r.token_hash = %s
        LIMIT 1
        """,
        (token_hash,),
    )
    return cursor.fetchone()


def invalidate_password_reset_tokens(cursor, user_id: int):
    cursor.execute(
        """
        UPDATE password_reset_tokens
        SET used_at = %s
        WHERE user_id = %s AND used_at IS NULL
        """,
        (current_datetime(), user_id),
    )


def invalidate_other_password_reset_tokens(cursor, user_id: int, keep_token_hash: str):
    cursor.execute(
        """
        UPDATE password_reset_tokens
        SET used_at = %s
        WHERE user_id = %s AND token_hash <> %s AND used_at IS NULL
        """,
        (current_datetime(), user_id, keep_token_hash),
    )


def delete_password_reset_token(cursor, token_hash: str):
    cursor.execute(
        """
        DELETE FROM password_reset_tokens
        WHERE token_hash = %s
        """,
        (token_hash,),
    )


def create_role_invitation_request(cursor, user_id: int, role: str, invited_by: int | None):
    raw_token = secrets.token_urlsafe(32)
    token_hash = role_invitation_token_hash(raw_token)
    now = current_datetime()
    expires_at = now + timedelta(hours=ROLE_INVITE_TOKEN_TTL_HOURS)

    cursor.execute(
        """
        INSERT INTO role_invitation_tokens (
            user_id, token_hash, invited_by, role, created_at, expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, token_hash, invited_by, role, now, expires_at),
    )
    return raw_token, expires_at, token_hash


def fetch_role_invitation_request(cursor, token: str):
    token_hash = role_invitation_token_hash(token)
    cursor.execute(
        """
        SELECT t.invitation_id, t.user_id, t.token_hash, t.role, t.created_at, t.expires_at, t.used_at,
               t.invited_by, u.full_name, u.email
        FROM role_invitation_tokens t
        JOIN users u ON u.user_id = t.user_id
        WHERE t.token_hash = %s
        LIMIT 1
        """,
        (token_hash,),
    )
    return cursor.fetchone()


def fetch_role_invitation_by_id(cursor, invitation_id: int):
    cursor.execute(
        """
        SELECT t.invitation_id, t.user_id, t.token_hash, t.role, t.created_at, t.expires_at, t.used_at,
               t.invited_by, u.full_name, u.email,
               inviter.full_name AS invited_by_name, inviter.email AS invited_by_email
        FROM role_invitation_tokens t
        JOIN users u ON u.user_id = t.user_id
        LEFT JOIN users inviter ON inviter.user_id = t.invited_by
        WHERE t.invitation_id = %s
        LIMIT 1
        """,
        (invitation_id,),
    )
    return cursor.fetchone()


def fetch_pending_role_invitations(cursor):
    cursor.execute(
        """
        SELECT t.invitation_id, t.user_id, t.role, t.created_at, t.expires_at, t.used_at,
               u.full_name, u.email,
               inviter.full_name AS invited_by_name, inviter.email AS invited_by_email
        FROM role_invitation_tokens t
        JOIN users u ON u.user_id = t.user_id
        LEFT JOIN users inviter ON inviter.user_id = t.invited_by
        WHERE t.used_at IS NULL
        ORDER BY t.created_at DESC, t.invitation_id DESC
        """
    )
    return cursor.fetchall()


def invalidate_role_invitation_tokens(cursor, user_id: int):
    cursor.execute(
        """
        UPDATE role_invitation_tokens
        SET used_at = %s
        WHERE user_id = %s AND used_at IS NULL
        """,
        (current_datetime(), user_id),
    )


def invalidate_other_role_invitation_tokens(cursor, user_id: int, keep_token_hash: str):
    cursor.execute(
        """
        UPDATE role_invitation_tokens
        SET used_at = %s
        WHERE user_id = %s AND token_hash <> %s AND used_at IS NULL
        """,
        (current_datetime(), user_id, keep_token_hash),
    )


def delete_role_invitation_token(cursor, token_hash: str):
    cursor.execute(
        """
        DELETE FROM role_invitation_tokens
        WHERE token_hash = %s
        """,
        (token_hash,),
    )


def build_initials(full_name: str) -> str:
    parts = [part for part in re.split(r"\s+", (full_name or "").strip()) if part]
    if not parts:
        return "CG"
    if len(parts) == 1:
        initials = re.sub(r"[^A-Za-z0-9]", "", parts[0])[:2].upper()
        return initials or "CG"

    initials = "".join(part[0] for part in parts[:2] if part and part[0].isalnum()).upper()
    return initials or "CG"


def normalize_review(review):
    author_name = (review.get("author_name") or review.get("user_full_name") or "Community Guest").strip() or "Community Guest"
    initials = (review.get("author_initials") or "").strip() or build_initials(author_name)

    try:
        rating = int(review.get("rating") or 5)
    except (TypeError, ValueError):
        rating = 5

    status = (review.get("status") or REVIEW_STATUS_PENDING).strip().title()
    if status not in REVIEW_STATUSES:
        status = REVIEW_STATUS_PENDING

    review["author_name"] = author_name
    review["author_initials"] = initials or "CG"
    review["rating"] = max(1, min(5, rating))
    review["content"] = (review.get("content") or "").strip()
    review["status"] = status
    return review


def lookup_user_id_by_name(cursor, full_name: str):
    full_name = (full_name or "").strip()
    if not full_name:
        return None

    cursor.execute(
        """
        SELECT user_id
        FROM users
        WHERE LOWER(full_name) = LOWER(%s)
        LIMIT 2
        """,
        (full_name,),
    )
    matches = cursor.fetchall()
    return matches[0]["user_id"] if len(matches) == 1 else None


def lookup_venue_id_by_name(cursor, venue_name: str):
    venue_name = (venue_name or "").strip()
    if not venue_name:
        return None

    cursor.execute(
        """
        SELECT venue_id
        FROM venues
        WHERE LOWER(venue_name) = LOWER(%s)
        LIMIT 2
        """,
        (venue_name,),
    )
    matches = cursor.fetchall()
    return matches[0]["venue_id"] if len(matches) == 1 else None


def lookup_category_id_by_name(cursor, category_name: str):
    category_name = (category_name or "").strip()
    if not category_name:
        return None

    cursor.execute(
        """
        SELECT category_id
        FROM categories
        WHERE LOWER(category_name) = LOWER(%s)
        LIMIT 2
        """,
        (category_name,),
    )
    matches = cursor.fetchall()
    return matches[0]["category_id"] if len(matches) == 1 else None


def normalize_optional_text(value, max_length=None):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None
    if max_length is not None:
        return text[:max_length]
    return text


def build_name_from_email(email: str) -> str:
    local_part = (email or "").strip().split("@", 1)[0]
    local_part = re.sub(r"[._-]+", " ", local_part)
    local_part = re.sub(r"\s+", " ", local_part).strip()
    if not local_part:
        return "Invited User"
    return local_part.title()


def fetch_or_create_invited_user(cursor, email: str):
    email = (email or "").strip().lower()
    cursor.execute(
        """
        SELECT user_id, full_name, email, phone, role, password_hash, password_changed_at
        FROM users
        WHERE LOWER(email) = LOWER(%s)
        LIMIT 1
        """,
        (email,),
    )
    user = cursor.fetchone()
    if user:
        user["email"] = (user.get("email") or email).strip().lower()
        return user, False

    now = current_datetime()
    full_name = build_name_from_email(email)
    cursor.execute(
        """
        INSERT INTO users (full_name, email, phone, password_hash, password_changed_at, role, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (full_name, email, None, None, now, ROLE_USER, now),
    )
    return {
        "user_id": cursor.lastrowid,
        "full_name": full_name,
        "email": email,
        "phone": None,
        "role": ROLE_USER,
        "password_hash": None,
        "password_changed_at": now,
    }, True


def local_static_image_filename(image_path):
    image_path = normalize_optional_text(image_path, 500)
    if not image_path or image_path.startswith("http"):
        return None
    if image_path.startswith("/static/"):
        return image_path[len("/static/") :]
    return image_path.lstrip("/")


def venue_image_gallery(venue_name: str, *, limit: int = 4):
    venue_name = (venue_name or "").strip().lower()
    if not venue_name or not VENUE_IMAGE_DIR.exists():
        return []

    matches = []
    for path in sorted(VENUE_IMAGE_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        stem = path.stem.strip().lower()
        if stem == venue_name or stem.startswith(venue_name):
            matches.append(f"images/venue/{path.name}")
            if len(matches) >= limit:
                break

    return matches


def merge_venue_into(cursor, source_name: str, target_name: str):
    source_id = lookup_venue_id_by_name(cursor, source_name)
    if not source_id:
        return

    target_id = lookup_venue_id_by_name(cursor, target_name)
    if not target_id:
        cursor.execute(
            """
            UPDATE venues
            SET venue_name=%s
            WHERE venue_id=%s
            """,
            (target_name, source_id),
        )
        return

    if source_id == target_id:
        return

    cursor.execute(
        """
        UPDATE events
        SET venue_id=%s
        WHERE venue_id=%s
        """,
        (target_id, source_id),
    )
    cursor.execute("DELETE FROM venues WHERE venue_id=%s", (source_id,))


def seed_default_reviews(cursor):
    """Seed demo reviews when the reviews table is empty."""
    cursor.execute(
        """
        SELECT seed_key
        FROM reviews
        WHERE seed_key IS NOT NULL AND TRIM(seed_key) <> ''
        """
    )
    existing_seed_keys = {row["seed_key"] for row in cursor.fetchall()}

    for seed in DEFAULT_REVIEWS:
        seed_key = (seed.get("seed_key") or "").strip()
        if not seed_key or seed_key in existing_seed_keys:
            continue

        author_name = (seed.get("author_name") or "Community Guest").strip() or "Community Guest"
        now = current_datetime()
        cursor.execute(
            """
            INSERT INTO reviews (
                seed_key, user_id, author_name, author_initials, rating, content, status,
                created_at, updated_at, reviewed_by, reviewed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                seed_key[:64],
                lookup_user_id_by_name(cursor, author_name),
                author_name[:255],
                (seed.get("author_initials") or build_initials(author_name))[:10],
                int(seed.get("rating") or 5),
                (seed.get("content") or "").strip(),
                REVIEW_STATUS_APPROVED,
                now,
                now,
                None,
                now,
            ),
    )


def seed_default_categories(cursor):
    """Seed the core event categories used by the demo events."""
    cursor.execute("SELECT category_name FROM categories")
    existing_categories = {
        (row["category_name"] or "").strip().lower()
        for row in cursor.fetchall()
        if row.get("category_name")
    }

    for seed in DEFAULT_CATEGORIES:
        category_name = (seed.get("category_name") or "").strip()
        if not category_name or category_name.lower() in existing_categories:
            continue

        cursor.execute(
            """
            INSERT INTO categories (category_name)
            VALUES (%s)
            """,
            (category_name[:100],),
        )
        existing_categories.add(category_name.lower())


def fetch_reviews(
    cursor,
    *,
    review_id=None,
    user_id=None,
    status=None,
    q="",
    limit=None,
    offset=None,
    public_only=False,
    order_by=None,
):
    filters = []
    params = []

    if review_id is not None:
        filters.append("r.review_id = %s")
        params.append(review_id)

    if user_id is not None:
        filters.append("r.user_id = %s")
        params.append(user_id)

    if status:
        filters.append("r.status = %s")
        params.append(status)
    elif public_only:
        filters.append("r.status = %s")
        params.append(REVIEW_STATUS_APPROVED)

    if q:
        like = f"%{q}%"
        filters.append(
            "(r.author_name LIKE %s OR r.content LIKE %s OR u.full_name LIKE %s OR u.email LIKE %s)"
        )
        params.extend([like, like, like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"
    order_clause = (
        order_by
        or (
            "COALESCE(r.reviewed_at, r.created_at) DESC, r.review_id DESC"
            if public_only
            else "r.created_at DESC, r.review_id DESC"
        )
    )
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""
    if offset is not None:
        limit_clause += f" OFFSET {int(offset)}"

    cursor.execute(
        f"""
        SELECT r.review_id, r.user_id, r.author_name, r.author_initials, r.rating,
               r.content, r.status, r.created_at, r.updated_at, r.reviewed_by, r.reviewed_at,
               u.full_name AS user_full_name, u.email AS user_email,
               moderator.full_name AS reviewed_by_name
        FROM reviews r
        LEFT JOIN users u ON r.user_id = u.user_id
        LEFT JOIN users moderator ON r.reviewed_by = moderator.user_id
        WHERE {where_clause}
        ORDER BY {order_clause}
        {limit_clause}
        """,
        tuple(params),
    )
    return [normalize_review(review) for review in cursor.fetchall()]


def count_reviews(
    cursor,
    *,
    user_id=None,
    status=None,
    q="",
    public_only=False,
):
    filters = []
    params = []

    if user_id is not None:
        filters.append("r.user_id = %s")
        params.append(user_id)

    if status:
        filters.append("r.status = %s")
        params.append(status)
    elif public_only:
        filters.append("r.status = %s")
        params.append(REVIEW_STATUS_APPROVED)

    if q:
        like = f"%{q}%"
        filters.append(
            "(r.author_name LIKE %s OR r.content LIKE %s OR u.full_name LIKE %s OR u.email LIKE %s)"
        )
        params.extend([like, like, like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"

    cursor.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM reviews r
        LEFT JOIN users u ON r.user_id = u.user_id
        WHERE {where_clause}
        """,
        tuple(params),
    )
    return cursor.fetchone()["count"] or 0


def calculate_satisfaction_rate(cursor):
    cursor.execute(
        """
        SELECT COALESCE(AVG(rating), 0) AS average_rating
        FROM reviews
        WHERE status = %s
        """,
        (REVIEW_STATUS_APPROVED,),
    )
    row = cursor.fetchone() or {}
    average_rating = row.get("average_rating") or 0

    try:
        average_rating = float(average_rating)
    except (TypeError, ValueError):
        average_rating = 0.0

    return max(0, min(100, int(round(average_rating * 20))))


def booking_booked_at_sql(alias="b"):
    return f"COALESCE({alias}.created_at, TIMESTAMP({alias}.booking_date, '00:00:00'))"


def fetch_venues_and_categories(cursor):
    cursor.execute(
        """
        SELECT venue_id, venue_name, address, city, suitable_for, image_url, capacity
        FROM venues
        ORDER BY
            CASE
                WHEN LOWER(venue_name) = 'ashton gate stadium' THEN 1
                WHEN LOWER(venue_name) = 'arnolfini' THEN 2
                WHEN LOWER(venue_name) = 'the bristol hippodrome' THEN 3
                WHEN LOWER(venue_name) = 'bristol old vic' THEN 4
                WHEN LOWER(venue_name) = 'bristol central library' THEN 5
                WHEN LOWER(venue_name) = 'royal west of england academy' THEN 6
                WHEN LOWER(venue_name) = 'uwe exhibition centre' THEN 7
                WHEN LOWER(venue_name) IN ('creative space a', 'creative space b', 'community centre a') THEN 9
                ELSE 8
            END,
            CASE
                WHEN LOWER(venue_name) = 'creative space a' THEN 1
                WHEN LOWER(venue_name) = 'creative space b' THEN 2
                WHEN LOWER(venue_name) = 'community centre a' THEN 3
                WHEN LOWER(venue_name) = 'ashton gate stadium'
                  OR LOWER(venue_name) = 'arnolfini'
                  OR LOWER(venue_name) = 'the bristol hippodrome'
                  OR LOWER(venue_name) = 'bristol old vic'
                  OR LOWER(venue_name) = 'bristol central library'
                  OR LOWER(venue_name) = 'royal west of england academy'
                  OR LOWER(venue_name) = 'uwe exhibition centre' THEN 0
                ELSE -venue_id
            END ASC,
            venue_name ASC
        """
    )
    venues = cursor.fetchall()
    cursor.execute(
        "SELECT category_id, category_name FROM categories ORDER BY category_name"
    )
    categories = cursor.fetchall()
    return venues, categories


def fetch_venue_overview(cursor, *, venue_id=None, q="", limit=None, offset=None):
    filters = []
    params = []

    if venue_id is not None:
        filters.append("v.venue_id = %s")
        params.append(venue_id)

    if q:
        like = f"%{q}%"
        filters.append(
            "(v.venue_name LIKE %s OR v.address LIKE %s OR v.city LIKE %s OR v.suitable_for LIKE %s)"
        )
        params.extend([like, like, like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"
    limit_clause = ""
    if limit is not None:
        limit_clause = f" LIMIT {int(limit)}"
        if offset is not None:
            limit_clause += f" OFFSET {int(offset)}"

    cursor.execute(
        f"""
        SELECT v.venue_id, v.venue_name, v.address, v.city, v.suitable_for, v.image_url, v.capacity,
               COALESCE(stats.total_events, 0) AS total_events,
               COALESCE(stats.upcoming_events, 0) AS upcoming_events,
               stats.next_event_date
        FROM venues v
        LEFT JOIN (
            SELECT venue_id,
                   COUNT(*) AS total_events,
                   SUM(CASE WHEN event_date >= CURDATE() THEN 1 ELSE 0 END) AS upcoming_events,
                   MIN(CASE WHEN event_date >= CURDATE() THEN event_date END) AS next_event_date
            FROM events
            GROUP BY venue_id
        ) stats ON v.venue_id = stats.venue_id
        WHERE {where_clause}
        ORDER BY
            v.venue_id DESC
        {limit_clause}
        """,
        tuple(params),
    )
    return cursor.fetchall()


def fetch_venue_details(cursor, venue_id: int):
    venues = fetch_venue_overview(cursor, venue_id=venue_id, limit=1)
    venue = venues[0] if venues else None
    if not venue:
        return None, []

    venue_images = venue_image_gallery(venue.get("venue_name"))
    primary_image = local_static_image_filename(venue.get("image_url"))
    if primary_image and primary_image not in venue_images:
        venue_images = [primary_image] + venue_images
    venue["venue_images"] = venue_images[:4]

    cursor.execute(
        """
        SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.location, e.price,
               e.event_capacity, c.category_name
        FROM events e
        LEFT JOIN categories c ON e.category_id = c.category_id
        WHERE e.venue_id = %s
        ORDER BY
            CASE WHEN e.event_date >= CURDATE() THEN 0 ELSE 1 END,
            e.event_date ASC,
            e.event_id DESC
        """,
        (venue_id,),
    )
    events = [enrich_booking_event(event) for event in cursor.fetchall()]
    return venue, events


def venue_exists(cursor, venue_id: int) -> bool:
    cursor.execute("SELECT 1 FROM venues WHERE venue_id=%s", (venue_id,))
    return cursor.fetchone() is not None


def venue_name_exists(cursor, venue_name: str, exclude_venue_id=None) -> bool:
    venue_name = (venue_name or "").strip()
    if not venue_name:
        return False

    params = [venue_name]
    exclusion = ""
    if exclude_venue_id is not None:
        exclusion = " AND venue_id <> %s"
        params.append(exclude_venue_id)

    cursor.execute(
        f"""
        SELECT 1
        FROM venues
        WHERE LOWER(TRIM(venue_name)) = LOWER(TRIM(%s))
        {exclusion}
        LIMIT 1
        """,
        tuple(params),
    )
    return cursor.fetchone() is not None


def category_exists(cursor, category_id: int) -> bool:
    cursor.execute("SELECT 1 FROM categories WHERE category_id=%s", (category_id,))
    return cursor.fetchone() is not None


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return cursor.fetchone() is not None


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def add_column_if_missing(cursor, table_name: str, column_name: str, definition: str):
    if not column_exists(cursor, table_name, column_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


# Database schema helpers.
def rename_column_if_needed(cursor, table_name: str, old_name: str, new_name: str, definition: str):
    if column_exists(cursor, table_name, old_name) and not column_exists(cursor, table_name, new_name):
        cursor.execute(
            f"ALTER TABLE {table_name} CHANGE COLUMN `{old_name}` `{new_name}` {definition}"
        )


def create_venues_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS venues (
            venue_id INT PRIMARY KEY AUTO_INCREMENT,
            venue_name VARCHAR(150) NOT NULL,
            address VARCHAR(255) NULL,
            city VARCHAR(100) NULL,
            suitable_for VARCHAR(255) NULL,
            image_url VARCHAR(500) NULL,
            capacity INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def seed_default_venues(cursor):
    """Seed the demo venues used by the event fixtures."""
    cursor.execute("SELECT venue_id, venue_name FROM venues")
    existing_venues = {}
    for row in cursor.fetchall():
        venue_name = normalize_optional_text(row.get("venue_name"), 150)
        if not venue_name:
            continue
        existing_venues[venue_name.lower()] = row["venue_id"]

    for seed in DEFAULT_VENUES:
        venue_name = normalize_optional_text(seed.get("venue_name"), 150)
        if not venue_name:
            continue

        venue_key = venue_name.lower()
        address = normalize_optional_text(seed.get("address"), 255) if "address" in seed else None
        city = normalize_optional_text(seed.get("city"), 100) if "city" in seed else None
        suitable_for = (
            normalize_optional_text(seed.get("suitable_for"), 255)
            if "suitable_for" in seed
            else None
        )
        image_url = (
            normalize_optional_text(seed.get("image_url"), 500)
            if "image_url" in seed
            else None
        )
        capacity = int(seed.get("capacity") or 0)

        if venue_key in existing_venues:
            venue_id = existing_venues[venue_key]
            update_parts = ["venue_name=%s"]
            update_values = [venue_name]

            if "address" in seed:
                update_parts.append("address=%s")
                update_values.append(address)
            if "city" in seed:
                update_parts.append("city=%s")
                update_values.append(city)
            if "suitable_for" in seed:
                update_parts.append("suitable_for=%s")
                update_values.append(suitable_for)
            if "image_url" in seed:
                update_parts.append("image_url=%s")
                update_values.append(image_url)
            if "capacity" in seed:
                update_parts.append("capacity=%s")
                update_values.append(capacity)

            update_values.append(venue_id)
            cursor.execute(
                f"""
                UPDATE venues
                SET {', '.join(update_parts)}
                WHERE venue_id=%s
                """,
                tuple(update_values),
            )
        else:
            cursor.execute(
                """
                INSERT INTO venues (venue_name, address, city, suitable_for, image_url, capacity)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    venue_name,
                    address,
                    city,
                    suitable_for,
                    image_url,
                    capacity,
                ),
            )
            existing_venues[venue_key] = cursor.lastrowid


def seed_default_events(cursor):
    """Seed future demo events that exercise multi-day booking logic."""
    cursor.execute(
        """
        SELECT event_name, event_date, venue_id
        FROM events
        """
    )
    existing_events = {
        (
            (row["event_name"] or "").strip().lower(),
            row["event_date"],
            row["venue_id"],
        )
        for row in cursor.fetchall()
        if row.get("event_name") and row.get("event_date") and row.get("venue_id")
    }

    for seed in DEFAULT_EVENTS:
        event_name = (seed.get("event_name") or "").strip()
        event_date = parse_event_date(seed.get("event_date"))
        event_end_date = parse_event_date(seed.get("event_end_date")) or event_date
        venue_id = lookup_venue_id_by_name(cursor, seed.get("venue_name"))
        category_id = lookup_category_id_by_name(cursor, seed.get("category_name"))

        if not event_name or not event_date or venue_id is None or category_id is None:
            continue

        key = (event_name.lower(), event_date, venue_id)
        if key in existing_events:
            continue

        cursor.execute(
            """
            INSERT INTO events (
                event_name, description, location, event_date, event_end_date, price,
                event_cost, venue_id, category_id, event_capacity, image_url
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_name[:255],
                (seed.get("description") or "").strip(),
                (seed.get("location") or "").strip()[:255],
                event_date,
                event_end_date,
                to_money(seed.get("price")),
                event_cost_from_price(seed.get("price")),
                venue_id,
                category_id,
                int(seed.get("event_capacity") or 0),
                (seed.get("image_url") or "").strip()[:255] or None,
            ),
        )
        existing_events.add(key)


def ensure_venues_table(cursor):
    if not table_exists(cursor, "venues"):
        create_venues_table(cursor)
    else:
        add_column_if_missing(cursor, "venues", "address", "VARCHAR(255) NULL AFTER venue_name")
        add_column_if_missing(cursor, "venues", "city", "VARCHAR(100) NULL AFTER address")
        add_column_if_missing(cursor, "venues", "suitable_for", "VARCHAR(255) NULL AFTER city")
        add_column_if_missing(cursor, "venues", "image_url", "VARCHAR(500) NULL AFTER suitable_for")
        add_column_if_missing(
            cursor,
            "venues",
            "capacity",
            "INT NOT NULL DEFAULT 0 AFTER image_url",
        )
        cursor.execute(
            """
            UPDATE venues
            SET city = 'Bristol'
            WHERE city IS NULL OR TRIM(city) = ''
            """
        )
        cursor.execute(
            """
            UPDATE venues
            SET capacity = 0
            WHERE capacity IS NULL OR capacity < 0
            """
        )

    seed_default_venues(cursor)
    merge_venue_into(cursor, "Harbourside Gallery", "Harbourside Art Space")
    merge_venue_into(cursor, "Harbourside Arts Hub", "Harbourside Art Space")
    merge_venue_into(cursor, "Bristol Creative Wharf", "Harbourside Art Space")
    merge_venue_into(cursor, "UWE Exhibition Hall", "UWE Exhibition Centre")


def ensure_event_venue_foreign_key(cursor):
    cursor.execute(
        """
        SELECT rc.CONSTRAINT_NAME, rc.DELETE_RULE
        FROM information_schema.REFERENTIAL_CONSTRAINTS rc
        JOIN information_schema.KEY_COLUMN_USAGE kcu
          ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
         AND rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
        WHERE rc.CONSTRAINT_SCHEMA = DATABASE()
          AND kcu.TABLE_NAME = 'events'
          AND kcu.COLUMN_NAME = 'venue_id'
          AND kcu.REFERENCED_TABLE_NAME = 'venues'
        GROUP BY rc.CONSTRAINT_NAME, rc.DELETE_RULE
        """
    )
    fk_rows = cursor.fetchall()
    needs_rebuild = not fk_rows or any((row.get("DELETE_RULE") or "").upper() != "SET NULL" for row in fk_rows)

    if not needs_rebuild:
        return

    for fk in fk_rows:
        cursor.execute(f"ALTER TABLE events DROP FOREIGN KEY `{fk['CONSTRAINT_NAME']}`")

    cursor.execute("ALTER TABLE events MODIFY COLUMN venue_id INT NULL")

    cursor.execute(
        """
        ALTER TABLE events
        ADD CONSTRAINT fk_events_venue
        FOREIGN KEY (venue_id) REFERENCES venues(venue_id) ON DELETE SET NULL
        """
    )


def create_waitlist_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS event_waitlist (
            waitlist_id INT PRIMARY KEY AUTO_INCREMENT,
            event_id INT NOT NULL,
            user_id INT NOT NULL,
            requested_tickets INT NOT NULL DEFAULT 1,
            booking_days INT NOT NULL DEFAULT 1,
            status VARCHAR(20) NOT NULL DEFAULT 'Waiting',
            offer_expires_at DATETIME NULL,
            booking_id INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL,
            FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def ensure_waitlist_table(cursor):
    if not table_exists(cursor, "event_waitlist"):
        create_waitlist_table(cursor)
        return

    add_column_if_missing(
        cursor,
        "event_waitlist",
        "requested_tickets",
        "INT NOT NULL DEFAULT 1 AFTER user_id",
    )
    add_column_if_missing(
        cursor,
        "event_waitlist",
        "booking_days",
        "INT NOT NULL DEFAULT 1 AFTER requested_tickets",
    )
    add_column_if_missing(
        cursor,
        "event_waitlist",
        "status",
        "VARCHAR(20) NOT NULL DEFAULT 'Waiting' AFTER booking_days",
    )
    add_column_if_missing(
        cursor,
        "event_waitlist",
        "offer_expires_at",
        "DATETIME NULL AFTER status",
    )
    add_column_if_missing(
        cursor,
        "event_waitlist",
        "booking_id",
        "INT NULL AFTER offer_expires_at",
    )
    add_column_if_missing(
        cursor,
        "event_waitlist",
        "created_at",
        "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER booking_id",
    )
    add_column_if_missing(
        cursor,
        "event_waitlist",
        "updated_at",
        "DATETIME NULL AFTER created_at",
    )


def create_contact_messages_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_messages (
            message_id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT NOT NULL,
            sender_name VARCHAR(255) NOT NULL,
            sender_email VARCHAR(255) NOT NULL,
            sender_phone VARCHAR(50),
            subject VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            admin_reply TEXT,
            replied_by INT NULL,
            replied_at DATETIME NULL,
            user_deleted_at DATETIME NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'New',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (replied_by) REFERENCES users(user_id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def ensure_contact_messages_table(cursor):
    if not table_exists(cursor, "contact_messages"):
        create_contact_messages_table(cursor)
        return

    rename_column_if_needed(cursor, "contact_messages", "id", "message_id", "INT NOT NULL AUTO_INCREMENT")
    rename_column_if_needed(cursor, "contact_messages", "name", "sender_name", "VARCHAR(255) NOT NULL")
    rename_column_if_needed(cursor, "contact_messages", "email", "sender_email", "VARCHAR(255) NOT NULL")

    add_column_if_missing(cursor, "contact_messages", "user_id", "INT NULL")
    add_column_if_missing(cursor, "contact_messages", "sender_phone", "VARCHAR(50) NULL")
    add_column_if_missing(cursor, "contact_messages", "admin_reply", "TEXT NULL")
    add_column_if_missing(cursor, "contact_messages", "replied_by", "INT NULL")
    add_column_if_missing(cursor, "contact_messages", "replied_at", "DATETIME NULL")
    add_column_if_missing(cursor, "contact_messages", "user_deleted_at", "DATETIME NULL")
    add_column_if_missing(
        cursor,
        "contact_messages",
        "status",
        "VARCHAR(20) NOT NULL DEFAULT 'New'",
    )
    add_column_if_missing(
        cursor,
        "contact_messages",
        "created_at",
        "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
    )


def create_reviews_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            review_id INT PRIMARY KEY AUTO_INCREMENT,
            seed_key VARCHAR(64) NULL,
            user_id INT NULL,
            author_name VARCHAR(255) NOT NULL DEFAULT 'Community Guest',
            author_initials VARCHAR(10) NOT NULL DEFAULT 'CG',
            rating TINYINT UNSIGNED NOT NULL DEFAULT 5,
            content TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'Pending',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL,
            reviewed_by INT NULL,
            reviewed_at DATETIME NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
            FOREIGN KEY (reviewed_by) REFERENCES users(user_id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def ensure_reviews_table(cursor):
    if not table_exists(cursor, "reviews"):
        create_reviews_table(cursor)
    else:
        add_column_if_missing(cursor, "reviews", "seed_key", "VARCHAR(64) NULL AFTER review_id")
        add_column_if_missing(cursor, "reviews", "user_id", "INT NULL AFTER review_id")
        add_column_if_missing(
            cursor,
            "reviews",
            "author_name",
            "VARCHAR(255) NOT NULL DEFAULT 'Community Guest' AFTER user_id",
        )
        add_column_if_missing(
            cursor,
            "reviews",
            "author_initials",
            "VARCHAR(10) NOT NULL DEFAULT 'CG' AFTER author_name",
        )
        add_column_if_missing(
            cursor,
            "reviews",
            "rating",
            "TINYINT UNSIGNED NOT NULL DEFAULT 5 AFTER author_initials",
        )
        add_column_if_missing(cursor, "reviews", "content", "TEXT NULL AFTER rating")
        add_column_if_missing(
            cursor,
            "reviews",
            "status",
            "VARCHAR(20) NOT NULL DEFAULT 'Pending' AFTER content",
        )
        add_column_if_missing(
            cursor,
            "reviews",
            "created_at",
            "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER status",
        )
        add_column_if_missing(cursor, "reviews", "updated_at", "DATETIME NULL AFTER created_at")
        add_column_if_missing(cursor, "reviews", "reviewed_by", "INT NULL AFTER updated_at")
        add_column_if_missing(cursor, "reviews", "reviewed_at", "DATETIME NULL AFTER reviewed_by")

        cursor.execute(
            """
            UPDATE reviews
            SET author_name = 'Community Guest'
            WHERE author_name IS NULL OR TRIM(author_name) = ''
            """
        )
        cursor.execute(
            """
            UPDATE reviews
            SET author_initials = 'CG'
            WHERE author_initials IS NULL OR TRIM(author_initials) = ''
            """
        )
        cursor.execute(
            """
            UPDATE reviews
            SET rating = 5
            WHERE rating IS NULL OR rating < 1 OR rating > 5
            """
        )
        cursor.execute(
            """
            UPDATE reviews
            SET status = 'Pending'
            WHERE status IS NULL OR TRIM(status) = '' OR status NOT IN ('Pending', 'Approved', 'Rejected')
            """
        )
        cursor.execute(
            """
            UPDATE reviews
            SET reviewed_at = created_at
            WHERE status = 'Approved' AND reviewed_at IS NULL
            """
        )

    cursor.execute("SHOW TABLES LIKE %s", ("testimonials",))
    legacy_testimonials = cursor.fetchone() is not None
    cursor.execute("SELECT COUNT(*) AS count FROM reviews")
    review_count = cursor.fetchone()["count"] or 0

    if legacy_testimonials and review_count == 0:
        cursor.execute(
            """
            SELECT *
            FROM testimonials
            ORDER BY created_at ASC, id ASC
            """
        )
        legacy_rows = cursor.fetchall()
        for legacy in legacy_rows:
            author_name = (legacy.get("author_name") or "Community Guest").strip() or "Community Guest"
            cursor.execute(
                """
                INSERT INTO reviews (
                    seed_key, user_id, author_name, author_initials, rating, content, status,
                    created_at, updated_at, reviewed_by, reviewed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    None,
                    lookup_user_id_by_name(cursor, author_name),
                    author_name[:255],
                    (legacy.get("author_initials") or build_initials(author_name))[:10],
                    int(legacy.get("rating") or 5),
                    (legacy.get("content") or "").strip(),
                    REVIEW_STATUS_APPROVED,
                    legacy.get("created_at") or current_datetime(),
                    legacy.get("created_at") or current_datetime(),
                    None,
                    legacy.get("created_at") or current_datetime(),
                ),
        )
    seed_default_reviews(cursor)


def fetch_contact_messages(
    cursor,
    user_id=None,
    message_id=None,
    q="",
    status="",
    limit=None,
    include_user_deleted=True,
):
    filters = []
    params = []

    if user_id is not None:
        filters.append("m.user_id = %s")
        params.append(user_id)

    if message_id is not None:
        filters.append("m.message_id = %s")
        params.append(message_id)

    if q:
        like = f"%{q}%"
        filters.append(
            "(m.sender_name LIKE %s OR m.sender_email LIKE %s OR m.subject LIKE %s OR m.message LIKE %s)"
        )
        params.extend([like, like, like, like])

    if status:
        filters.append("m.status = %s")
        params.append(status)

    if not include_user_deleted:
        filters.append("m.user_deleted_at IS NULL")

    where_clause = " AND ".join(filters) if filters else "1=1"
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""

    cursor.execute(
        f"""
        SELECT m.message_id, m.user_id, m.sender_name, m.sender_email, m.sender_phone,
               m.subject, m.message, m.admin_reply, m.status, m.created_at, m.replied_at,
               m.replied_by, admin.full_name AS replied_by_name
        FROM contact_messages m
        LEFT JOIN users admin ON m.replied_by = admin.user_id
        WHERE {where_clause}
        ORDER BY m.created_at DESC, m.message_id DESC
        {limit_clause}
        """,
        tuple(params),
    )
    return cursor.fetchall()


def fetch_contact_message(cursor, message_id: int):
    messages = fetch_contact_messages(cursor, message_id=message_id, limit=1)
    return messages[0] if messages else None


def count_contact_messages(cursor, status=None):
    filters = []
    params = []

    if status:
        filters.append("status = %s")
        params.append(status)

    where_clause = " AND ".join(filters) if filters else "1=1"

    cursor.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM contact_messages
        WHERE {where_clause}
        """,
        tuple(params),
    )
    return cursor.fetchone()["count"] or 0


def fetch_newsletter_subscribers(cursor, limit=None):
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""

    cursor.execute(
        f"""
        SELECT subscriber_id, email, created_at
        FROM newsletter_subscribers
        ORDER BY created_at DESC, subscriber_id DESC
        {limit_clause}
        """
    )
    return cursor.fetchall()


def create_newsletter_subscribers_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS newsletter_subscribers (
            subscriber_id INT PRIMARY KEY AUTO_INCREMENT,
            email VARCHAR(255) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_newsletter_subscribers_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def ensure_newsletter_subscribers_table(cursor):
    if not table_exists(cursor, "newsletter_subscribers"):
        create_newsletter_subscribers_table(cursor)
        return

    add_column_if_missing(
        cursor,
        "newsletter_subscribers",
        "created_at",
        "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER email",
    )


def create_password_reset_tokens_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            reset_token_id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT NOT NULL,
            token_hash CHAR(64) NOT NULL,
            requested_by INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            used_at DATETIME NULL,
            UNIQUE KEY uq_password_reset_tokens_token_hash (token_hash),
            KEY idx_password_reset_tokens_user_id (user_id),
            KEY idx_password_reset_tokens_expires_at (expires_at),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (requested_by) REFERENCES users(user_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def create_role_invitation_tokens_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS role_invitation_tokens (
            invitation_id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT NOT NULL,
            token_hash CHAR(64) NOT NULL,
            invited_by INT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'user',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            used_at DATETIME NULL,
            UNIQUE KEY uq_role_invitation_tokens_token_hash (token_hash),
            KEY idx_role_invitation_tokens_user_id (user_id),
            KEY idx_role_invitation_tokens_expires_at (expires_at),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (invited_by) REFERENCES users(user_id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def ensure_password_reset_tokens_table(cursor):
    if not table_exists(cursor, "password_reset_tokens"):
        create_password_reset_tokens_table(cursor)
        return

    add_column_if_missing(cursor, "password_reset_tokens", "user_id", "INT NOT NULL AFTER reset_token_id")
    add_column_if_missing(cursor, "password_reset_tokens", "token_hash", "CHAR(64) NOT NULL AFTER user_id")
    add_column_if_missing(cursor, "password_reset_tokens", "requested_by", "INT NULL AFTER token_hash")
    add_column_if_missing(
        cursor,
        "password_reset_tokens",
        "created_at",
        "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER requested_by",
    )
    add_column_if_missing(cursor, "password_reset_tokens", "expires_at", "DATETIME NOT NULL AFTER created_at")
    add_column_if_missing(cursor, "password_reset_tokens", "used_at", "DATETIME NULL AFTER expires_at")


def ensure_role_invitation_tokens_table(cursor):
    if not table_exists(cursor, "role_invitation_tokens"):
        create_role_invitation_tokens_table(cursor)
        return

    add_column_if_missing(cursor, "role_invitation_tokens", "user_id", "INT NOT NULL AFTER invitation_id")
    add_column_if_missing(cursor, "role_invitation_tokens", "token_hash", "CHAR(64) NOT NULL AFTER user_id")
    add_column_if_missing(cursor, "role_invitation_tokens", "invited_by", "INT NULL AFTER token_hash")
    add_column_if_missing(cursor, "role_invitation_tokens", "role", "VARCHAR(20) NOT NULL DEFAULT 'user' AFTER invited_by")
    add_column_if_missing(
        cursor,
        "role_invitation_tokens",
        "created_at",
        "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER role",
    )
    add_column_if_missing(cursor, "role_invitation_tokens", "expires_at", "DATETIME NOT NULL AFTER created_at")
    add_column_if_missing(cursor, "role_invitation_tokens", "used_at", "DATETIME NULL AFTER expires_at")


def seed_default_newsletter_subscribers(cursor):
    cursor.execute("SELECT COUNT(*) AS count FROM newsletter_subscribers")
    subscriber_count = cursor.fetchone()["count"] or 0
    if subscriber_count > 0:
        return

    cursor.execute(
        """
        SELECT LOWER(TRIM(email)) AS email, MIN(created_at) AS created_at
        FROM users
        WHERE email IS NOT NULL AND TRIM(email) <> ''
        GROUP BY LOWER(TRIM(email))
        ORDER BY MIN(user_id) ASC
        LIMIT 5
        """
    )
    seed_rows = [
        {
            "email": row["email"],
            "created_at": row["created_at"] or current_datetime(),
        }
        for row in cursor.fetchall()
        if row.get("email")
    ]
    if not seed_rows and DEFAULT_ADMIN_EMAIL:
        seed_rows = [{"email": DEFAULT_ADMIN_EMAIL.lower(), "created_at": current_datetime()}]

    for row in seed_rows:
        cursor.execute(
            """
            INSERT IGNORE INTO newsletter_subscribers (email, created_at)
            VALUES (%s, %s)
            """,
            (row["email"], row["created_at"]),
        )


def create_schema_migrations_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_key VARCHAR(100) PRIMARY KEY,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def schema_migration_applied(cursor, migration_key: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_key = %s",
        (migration_key,),
    )
    return cursor.fetchone() is not None


def mark_schema_migration_applied(cursor, migration_key: str):
    cursor.execute(
        "INSERT IGNORE INTO schema_migrations (migration_key) VALUES (%s)",
        (migration_key,),
    )


def backfill_event_costs_from_price(cursor):
    cursor.execute(
        """
        UPDATE events
        SET event_cost = CASE
            WHEN price IS NOT NULL AND price > 0 THEN ROUND(price * %s, 2)
            ELSE 0
        END
        WHERE event_cost IS NULL
           OR event_cost <= 0
        """,
        (EVENT_COST_MARGIN,),
    )


def initialize_database():
    """Apply lightweight migrations and seed demo data once per process."""
    global _db_initialized
    if _db_initialized:
        return

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        add_column_if_missing(cursor, "events", "description", "TEXT NULL AFTER event_name")
        add_column_if_missing(cursor, "events", "location", "VARCHAR(255) NULL AFTER description")
        add_column_if_missing(cursor, "events", "event_end_date", "DATE NULL AFTER event_date")
        add_column_if_missing(cursor, "events", "conditions", "TEXT NULL AFTER event_end_date")
        add_column_if_missing(
            cursor,
            "events",
            "event_cost",
            "DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER event_capacity",
        )
        add_column_if_missing(
            cursor,
            "events",
            "image_url",
            "VARCHAR(255) NULL AFTER event_cost",
        )
        add_column_if_missing(cursor, "users", "password_hash", "VARCHAR(255) NULL AFTER email")
        add_column_if_missing(
            cursor,
            "users",
            "password_changed_at",
            "DATETIME NULL AFTER password_hash",
        )
        add_column_if_missing(
            cursor,
            "users",
            "role",
            "VARCHAR(20) NOT NULL DEFAULT 'user' AFTER password_hash",
        )
        add_column_if_missing(
            cursor,
            "users",
            "created_at",
            "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER phone",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "status",
            "VARCHAR(20) NOT NULL DEFAULT 'Confirmed' AFTER discount_applied",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "waitlist_id",
            "INT NULL AFTER event_id",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "contact_phone",
            "VARCHAR(50) NULL AFTER waitlist_id",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "booking_days",
            "INT NOT NULL DEFAULT 1 AFTER tickets",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "subtotal_amount",
            "DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER booking_days",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "student_discount_amount",
            "DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER subtotal_amount",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "advance_discount_amount",
            "DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER student_discount_amount",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "cancellation_charge",
            "DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER advance_discount_amount",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "refund_amount",
            "DECIMAL(10,2) NOT NULL DEFAULT 0.00 AFTER cancellation_charge",
        )
        add_column_if_missing(
            cursor,
            "bookings",
            "created_at",
            "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER status",
        )
        add_column_if_missing(
            cursor,
            "payments",
            "payment_method",
            "VARCHAR(50) NULL AFTER amount",
        )
        add_column_if_missing(
            cursor,
            "payments",
            "payment_source",
            "VARCHAR(255) NULL AFTER payment_method",
        )
        create_schema_migrations_table(cursor)
        ensure_venues_table(cursor)
        seed_default_categories(cursor)
        seed_default_events(cursor)
        ensure_event_venue_foreign_key(cursor)
        ensure_waitlist_table(cursor)
        ensure_contact_messages_table(cursor)
        ensure_reviews_table(cursor)
        ensure_newsletter_subscribers_table(cursor)
        ensure_password_reset_tokens_table(cursor)
        ensure_role_invitation_tokens_table(cursor)

        cursor.execute(
            """
            UPDATE events
            SET event_end_date = event_date
            WHERE event_end_date IS NULL AND event_date IS NOT NULL
            """
        )
        if not schema_migration_applied(cursor, "event_cost_backfill_from_price_v1"):
            backfill_event_costs_from_price(cursor)
            mark_schema_migration_applied(cursor, "event_cost_backfill_from_price_v1")
        cursor.execute("UPDATE users SET role=%s WHERE role IS NULL OR role=''", (ROLE_USER,))
        cursor.execute(
            """
            UPDATE users
            SET password_changed_at = COALESCE(password_changed_at, created_at, CURRENT_TIMESTAMP)
            WHERE password_changed_at IS NULL
            """
        )
        cursor.execute(
            "UPDATE bookings SET status='Confirmed' WHERE status IS NULL OR status=''"
        )
        cursor.execute(
            """
            UPDATE bookings
            SET booking_days = 1
            WHERE booking_days IS NULL OR booking_days < 1
            """
        )
        cursor.execute(
            """
            UPDATE bookings b
            JOIN users u ON u.user_id = b.user_id
            SET b.contact_phone = COALESCE(b.contact_phone, u.phone)
            WHERE b.contact_phone IS NULL OR b.contact_phone = ''
            """
        )
        cursor.execute(
            """
            UPDATE bookings
            SET subtotal_amount = COALESCE(subtotal_amount, 0),
                student_discount_amount = COALESCE(student_discount_amount, 0),
                advance_discount_amount = COALESCE(advance_discount_amount, 0),
                cancellation_charge = COALESCE(cancellation_charge, 0),
                refund_amount = COALESCE(refund_amount, 0)
            """
        )
        cursor.execute("UPDATE contact_messages SET status='New' WHERE status IS NULL OR status=''")
        cursor.execute(
            """
            UPDATE contact_messages
            SET status='Replied'
            WHERE (status IS NULL OR status='') AND admin_reply IS NOT NULL AND admin_reply <> ''
            """
        )

        bootstrap_default_admin(cursor)
        seed_default_newsletter_subscribers(cursor)
        conn.commit()
        _db_initialized = True
    finally:
        cursor.close()
        conn.close()


def bootstrap_default_admin(cursor):
    cursor.execute(
        """
        SELECT user_id, password_hash, role, password_changed_at
        FROM users
        WHERE email = %s
        """,
        (DEFAULT_ADMIN_EMAIL,),
    )
    admin_user = cursor.fetchone()

    if admin_user:
        updates = []
        params = []

        if (admin_user.get("role") or ROLE_USER).lower() != ROLE_ADMIN:
            updates.append("role=%s")
            params.append(ROLE_ADMIN)

        if not admin_user.get("password_hash"):
            updates.append("password_hash=%s")
            params.append(generate_password_hash(DEFAULT_ADMIN_PASSWORD))

        if not admin_user.get("password_changed_at"):
            updates.append("password_changed_at=%s")
            params.append(current_datetime())

        if updates:
            cursor.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE user_id=%s",
                tuple(params + [admin_user["user_id"]]),
            )
        return

    cursor.execute(
        """
        INSERT INTO users (full_name, email, phone, password_hash, password_changed_at, role, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            DEFAULT_ADMIN_NAME,
            DEFAULT_ADMIN_EMAIL,
            "",
            generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            current_datetime(),
            ROLE_ADMIN,
            current_datetime(),
        ),
    )


def load_current_user():
    """Load the signed-in user into Flask's request context."""
    user_id = session.get("user_id")
    if not user_id:
        g.current_user = None
        return

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT user_id, full_name, email, phone, role, created_at, password_changed_at
        FROM users
        WHERE user_id = %s
        """,
        (user_id,),
    )
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        session.clear()
        g.current_user = None
        return

    session_version = session.get(PASSWORD_VERSION_SESSION_KEY)
    current_version = serialize_password_version(user.get("password_changed_at"))
    if session_version != current_version:
        session.clear()
        g.current_user = None
        return

    session["user_role"] = user["role"]
    session["user_name"] = user["full_name"]
    session[PASSWORD_VERSION_SESSION_KEY] = current_version
    g.current_user = user


def load_admin_sidebar_data():
    current_user = getattr(g, "current_user", None)
    if not current_user or current_user.get("role") != ROLE_ADMIN:
        g.newsletter_subscribers = []
        g.newsletter_subscriber_count = 0
        return

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        subscribers = fetch_newsletter_subscribers(cursor)
        g.newsletter_subscribers = subscribers
        g.newsletter_subscriber_count = len(subscribers)
    except mysql.connector.Error:
        g.newsletter_subscribers = []
        g.newsletter_subscriber_count = 0
    finally:
        cursor.close()
        conn.close()


@app.before_request
def prepare_request_context():
    initialize_database()
    load_current_user()
    load_admin_sidebar_data()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        expire_waitlist_offers(cursor, conn)
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


@app.context_processor
def inject_template_globals():
    current_user = getattr(g, "current_user", None)
    return {
        "current_user": current_user,
        "is_authenticated": current_user is not None,
        "is_admin": bool(current_user and current_user.get("role") == ROLE_ADMIN),
        "newsletter_subscribers": getattr(g, "newsletter_subscribers", []),
        "newsletter_subscriber_count": getattr(g, "newsletter_subscriber_count", 0),
        "student_id_disclaimer": STUDENT_ID_DISCLAIMER,
    }


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not g.current_user:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=current_request_path()))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not g.current_user:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=current_request_path()))
        if g.current_user.get("role") != ROLE_ADMIN:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def booking_totals_join(alias="bt"):
    return f"""
        LEFT JOIN (
            SELECT event_id, SUM(tickets) AS booked_tickets
            FROM bookings
            WHERE {ACTIVE_BOOKING_CONDITION}
            GROUP BY event_id
        ) {alias} ON e.event_id = {alias}.event_id
    """


def booking_revenue_total_sql(alias="b"):
    return (
        f"COALESCE(SUM(CASE WHEN COALESCE({alias}.status, 'Confirmed') <> 'Cancelled' "
        f"THEN GREATEST(COALESCE({alias}.subtotal_amount, 0) - COALESCE({alias}.discount_applied, 0), 0) "
        f"ELSE 0 END), 0)"
    )


def build_pagination_pages(current_page: int, total_pages: int, window: int = 2):
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    pages = []
    start = max(1, current_page - window)
    end = min(total_pages, current_page + window)

    if start > 1:
        pages.append(1)
        if start > 2:
            pages.append(None)

    pages.extend(range(start, end + 1))

    if end < total_pages:
        if end < total_pages - 1:
            pages.append(None)
        pages.append(total_pages)

    return pages


def is_allowed_event_image_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EVENT_IMAGE_EXTENSIONS


def save_event_image_upload(uploaded_file):
    filename = secure_filename(uploaded_file.filename or "")
    if not filename:
        raise ValueError("Please choose an image file.")
    if not is_allowed_event_image_filename(filename):
        raise ValueError("Please upload a JPG, JPEG, PNG, GIF, or WebP image.")

    EVENT_IMAGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{Path(filename).suffix.lower()}"
    file_path = EVENT_IMAGE_UPLOAD_DIR / stored_name
    uploaded_file.save(str(file_path))
    return f"/static/images/event/uploads/{stored_name}"


def remove_uploaded_event_image(image_url):
    image_url = (image_url or "").strip()
    if not image_url:
        return

    relative_path = image_url[len("/static/") :] if image_url.startswith("/static/") else image_url.lstrip("/")
    if not relative_path.startswith("images/event/uploads/"):
        return

    file_name = Path(relative_path).name
    if not file_name:
        return

    file_path = EVENT_IMAGE_UPLOAD_DIR / file_name
    try:
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
    except OSError:
        pass


def fetch_event(cursor, event_id: int):
    cursor.execute(
        f"""
        SELECT e.*, v.venue_name, v.address, c.category_name,
               COALESCE(bt.booked_tickets, 0) AS booked_tickets,
               CASE
                    WHEN e.event_capacity IS NULL THEN NULL
                    ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
               END AS remaining_seats
        FROM events e
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        {booking_totals_join()}
        WHERE e.event_id = %s
        """,
        (event_id,),
    )
    return cursor.fetchone()


def available_seats(cursor, event_id: int, exclude_booking_id=None):
    cursor.execute("SELECT event_capacity FROM events WHERE event_id=%s", (event_id,))
    event = cursor.fetchone()
    if not event:
        return None

    event_capacity = event.get("event_capacity")
    if event_capacity is None:
        return None

    params = [event_id]
    exclusion = ""
    if exclude_booking_id is not None:
        exclusion = " AND booking_id <> %s"
        params.append(exclude_booking_id)

    cursor.execute(
        f"""
        SELECT COALESCE(SUM(tickets), 0) AS booked
        FROM bookings
        WHERE event_id=%s
          AND {ACTIVE_BOOKING_CONDITION}
          {exclusion}
        """,
        tuple(params),
    )
    booked_total = int(cursor.fetchone()["booked"] or 0)
    return max(int(event_capacity) - booked_total, 0)


def fetch_booking_details(cursor, booking_id: int):
    booked_at_sql = booking_booked_at_sql()
    cursor.execute(
        f"""
        SELECT b.booking_id, b.user_id, b.event_id, b.waitlist_id, b.contact_phone, b.booking_date, b.created_at,
               {booked_at_sql} AS booked_at,
               b.tickets, b.booking_days, b.is_student,
               b.subtotal_amount, b.student_discount_amount, b.advance_discount_amount,
               b.discount_applied, b.cancellation_charge, b.refund_amount,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email, u.phone, u.role,
               e.event_name, e.event_date, e.event_end_date, e.location, e.price, e.event_capacity,
               e.conditions, e.event_cost,
               v.venue_name, v.address,
               c.category_name,
               p.payment_id, p.amount, p.payment_method, p.payment_source,
               COALESCE(p.payment_status, 'Pending') AS payment_status,
               p.payment_date
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        WHERE b.booking_id = %s
        """,
        (booking_id,),
    )
    return cursor.fetchone()


def count_booking_receipts(cursor, q=""):
    filters = []
    params = []

    if q:
        like = f"%{q}%"
        filters.append(
            """
            (
                CAST(b.booking_id AS CHAR) LIKE %s
                OR CONCAT('BCE-', LPAD(b.booking_id, 6, '0')) LIKE %s
                OR u.full_name LIKE %s
                OR u.email LIKE %s
                OR e.event_name LIKE %s
            )
            """
        )
        params.extend([like, like, like, like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"

    cursor.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        WHERE {where_clause}
        """,
        tuple(params),
    )
    return cursor.fetchone()["count"] or 0


def fetch_booking_receipts(cursor, *, q="", limit=None, offset=None):
    filters = []
    params = []

    if q:
        like = f"%{q}%"
        filters.append(
            """
            (
                CAST(b.booking_id AS CHAR) LIKE %s
                OR CONCAT('BCE-', LPAD(b.booking_id, 6, '0')) LIKE %s
                OR u.full_name LIKE %s
                OR u.email LIKE %s
                OR e.event_name LIKE %s
            )
            """
        )
        params.extend([like, like, like, like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"
    booked_at_sql = booking_booked_at_sql()
    limit_clause = ""
    if limit is not None:
        limit_clause = f" LIMIT {int(limit)}"
        if offset is not None:
            limit_clause += f" OFFSET {int(offset)}"

    cursor.execute(
        f"""
        SELECT b.booking_id, b.booking_date, b.created_at, {booked_at_sql} AS booked_at,
               b.tickets, b.booking_days, b.is_student,
               b.subtotal_amount, b.student_discount_amount, b.advance_discount_amount,
               b.discount_applied, b.cancellation_charge, b.refund_amount,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_id, e.event_name, e.event_date, e.event_end_date,
               p.amount, p.payment_method, p.payment_source, COALESCE(p.payment_status, 'Pending') AS payment_status
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        WHERE {where_clause}
        ORDER BY booked_at DESC, b.booking_id DESC
        {limit_clause}
        """,
        tuple(params),
    )
    return cursor.fetchall()


def fetch_waitlist_entries(
    cursor,
    *,
    waitlist_id=None,
    user_id=None,
    event_id=None,
    status=None,
    limit=None,
    offset=None,
    order_by=None,
):
    filters = []
    params = []

    if waitlist_id is not None:
        filters.append("w.waitlist_id = %s")
        params.append(waitlist_id)

    if user_id is not None:
        filters.append("w.user_id = %s")
        params.append(user_id)

    if event_id is not None:
        filters.append("w.event_id = %s")
        params.append(event_id)

    if status:
        filters.append("w.status = %s")
        params.append(status)

    where_clause = " AND ".join(filters) if filters else "1=1"
    limit_clause = ""
    if limit is not None:
        limit_clause = f" LIMIT {int(limit)}"
        if offset is not None:
            limit_clause += f" OFFSET {int(offset)}"

    default_order = "CASE WHEN w.status = 'Offered' THEN 0 ELSE 1 END, w.created_at ASC, w.waitlist_id ASC"

    cursor.execute(
        f"""
        SELECT w.waitlist_id, w.event_id, w.user_id, w.requested_tickets, w.booking_days,
               w.status, w.offer_expires_at, w.booking_id, w.created_at, w.updated_at,
               e.event_name, e.event_date, e.event_end_date, e.location, e.price,
               v.venue_name, c.category_name,
               u.full_name, u.email, u.phone
        FROM event_waitlist w
        JOIN events e ON w.event_id = e.event_id
        JOIN users u ON w.user_id = u.user_id
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        WHERE {where_clause}
        ORDER BY {order_by or default_order}
        {limit_clause}
        """,
        tuple(params),
    )
    return cursor.fetchall()


def fetch_waitlist_entry(cursor, waitlist_id: int):
    rows = fetch_waitlist_entries(cursor, waitlist_id=waitlist_id, limit=1)
    return rows[0] if rows else None


def fetch_active_waitlist_entries(cursor, *, limit=None, order_by=None):
    entries = fetch_waitlist_entries(
        cursor,
        status=None,
        limit=limit,
        order_by=order_by or "CASE WHEN w.status = 'Offered' THEN 0 ELSE 1 END, w.created_at ASC, w.waitlist_id ASC",
    )
    return [
        entry
        for entry in entries
        if (entry.get("status") or "") in {WAITLIST_STATUS_WAITING, WAITLIST_STATUS_OFFERED}
    ]


def expire_waitlist_offers(cursor, conn):
    now = current_datetime()
    cursor.execute(
        """
        SELECT waitlist_id, event_id
        FROM event_waitlist
        WHERE status=%s
          AND offer_expires_at IS NOT NULL
          AND offer_expires_at < %s
        ORDER BY offer_expires_at ASC, created_at ASC, waitlist_id ASC
        """,
        (WAITLIST_STATUS_OFFERED, now),
    )
    expired_rows = cursor.fetchall()
    if not expired_rows:
        return []

    affected_event_ids = []
    seen_event_ids = set()

    for row in expired_rows:
        cursor.execute(
            """
            UPDATE event_waitlist
            SET status=%s,
                offer_expires_at=NULL,
                updated_at=%s
            WHERE waitlist_id=%s
            """,
            (WAITLIST_STATUS_EXPIRED, now, row["waitlist_id"]),
        )
        event_id = row.get("event_id")
        if event_id not in seen_event_ids:
            seen_event_ids.add(event_id)
            affected_event_ids.append(event_id)

    promoted_waitlist_ids = []
    for event_id in affected_event_ids:
        promoted_waitlist_ids.extend(promote_waitlist_entries(cursor, conn, event_id))

    return promoted_waitlist_ids


def waitlist_offer_is_active(waitlist_entry) -> bool:
    if not waitlist_entry:
        return False
    if (waitlist_entry.get("status") or "") != WAITLIST_STATUS_OFFERED:
        return False
    offer_expires_at = waitlist_entry.get("offer_expires_at")
    if not offer_expires_at:
        return True
    return offer_expires_at >= current_datetime()


def build_pending_booking_from_waitlist(waitlist_entry, event):
    if not waitlist_entry or not event:
        return None

    booked_at = current_datetime()
    pricing = calculate_booking_breakdown(
        event.get("price"),
        waitlist_entry.get("requested_tickets") or 1,
        False,
        booking_days=waitlist_entry.get("booking_days") or 1,
        event_duration=event_duration_days(event),
        event_date=event.get("event_date"),
        booked_at=booked_at,
    )

    return {
        "waitlist_id": waitlist_entry["waitlist_id"],
        "event_id": int(event["event_id"]),
        "return_url": url_for("my_bookings"),
        "event_name": event.get("event_name") or "",
        "event_date": event["event_date"].isoformat() if event.get("event_date") else "",
        "event_end_date": event["event_end_date"].isoformat() if event.get("event_end_date") else "",
        "event_date_label": event_date_range_label(event),
        "venue_name": event.get("venue_name") or "",
        "location": event.get("location") or "",
        "price": str(to_money(event.get("price"))),
        "tickets": int(waitlist_entry.get("requested_tickets") or 1),
        "booking_days": int(waitlist_entry.get("booking_days") or 1),
        "phone": (waitlist_entry.get("phone") or "").strip(),
        "is_student": False,
        "payment_method": "card",
        "payment_method_label": payment_method_label("card"),
        "booking_date": booked_at.isoformat(timespec="seconds"),
        "subtotal": str(pricing["base_subtotal"]),
        "student_discount": str(pricing["student_discount"]),
        "advance_discount": str(pricing["advance_discount"]),
        "discount": str(pricing["discount_total"]),
        "total_amount": str(pricing["total"]),
        "remaining_seats": event.get("remaining_seats"),
        "max_tickets": booking_ticket_limit(event),
        "event_duration_days": event_duration_days(event),
        "event_cost": str(to_money(event.get("event_cost"))),
    }


def promote_waitlist_entries(cursor, conn, event_id: int):
    seats_available = available_seats(cursor, event_id)
    if seats_available is None or seats_available <= 0:
        return []

    cursor.execute(
        """
        SELECT waitlist_id, requested_tickets
        FROM event_waitlist
        WHERE event_id=%s AND status=%s
        ORDER BY created_at ASC, waitlist_id ASC
        """,
        (event_id, WAITLIST_STATUS_WAITING),
    )
    waiting_rows = cursor.fetchall()
    offered = []
    now = current_datetime()

    for row in waiting_rows:
        requested_tickets = int(row.get("requested_tickets") or 1)
        if requested_tickets > seats_available:
            continue

        cursor.execute(
            """
            UPDATE event_waitlist
            SET status=%s,
                offer_expires_at=%s,
                updated_at=%s
            WHERE waitlist_id=%s
            """,
            (
                WAITLIST_STATUS_OFFERED,
                now + timedelta(days=WAITLIST_OFFER_HOLD_DAYS),
                now,
                row["waitlist_id"],
            ),
        )
        offered.append(row["waitlist_id"])
        seats_available -= requested_tickets
        if seats_available <= 0:
            break

    return offered


def event_price_reduction_summary(cursor, event_id: int):
    booked_at_sql = booking_booked_at_sql()
    cursor.execute(
        f"""
        SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.price,
               COUNT(b.booking_id) AS total_bookings,
               COALESCE(
                   SUM(
                       CASE
                           WHEN DATE({booked_at_sql}) >= DATE_SUB(COALESCE(e.event_date, CURDATE()), INTERVAL %s DAY)
                           THEN 1 ELSE 0
                       END
                   ),
                   0
               ) AS recent_bookings
        FROM events e
        LEFT JOIN bookings b
               ON b.event_id = e.event_id
              AND COALESCE(b.status, 'Confirmed') <> 'Cancelled'
        WHERE e.event_id = %s
        GROUP BY e.event_id, e.event_name, e.event_date, e.event_end_date, e.price
        """,
        (EVENT_PRICE_REDUCTION_LOOKBACK_DAYS, event_id),
    )
    row = cursor.fetchone()
    if not row:
        return None

    total_bookings = int(row.get("total_bookings") or 0)
    recent_bookings = int(row.get("recent_bookings") or 0)
    eligible = (
        total_bookings > 0
        and Decimal(recent_bookings) / Decimal(total_bookings) < EVENT_PRICE_REDUCTION_BOOKING_RATIO
    )
    current_price = to_money(row.get("price"))
    reduced_price = (current_price * (Decimal("1.00") - EVENT_PRICE_REDUCTION_THRESHOLD)).quantize(
        Decimal("0.01")
    )

    return {
        "event_id": row["event_id"],
        "event_name": row["event_name"],
        "event_date": row["event_date"],
        "event_end_date": row.get("event_end_date"),
        "current_price": current_price,
        "reduced_price": reduced_price,
        "total_bookings": total_bookings,
        "recent_bookings": recent_bookings,
        "eligible": eligible,
        "recent_ratio": (Decimal(recent_bookings) / Decimal(total_bookings)) if total_bookings else Decimal("0"),
    }


def can_access_booking(booking) -> bool:
    if not booking or not g.current_user:
        return False
    if g.current_user.get("role") == ROLE_ADMIN:
        return True
    return booking.get("user_id") == g.current_user.get("user_id")


def booking_is_user_editable(booking) -> bool:
    if not can_access_booking(booking):
        return False
    if (booking.get("status") or "").strip() == "Cancelled":
        return False

    event_date = booking.get("event_date")
    if not event_date:
        return False

    return event_date >= current_date()


def booking_edit_max_tickets(cursor, booking, booking_id: int):
    seats_available = available_seats(cursor, booking["event_id"], exclude_booking_id=booking_id)
    if seats_available is None:
        return 10

    try:
        seats_available = int(seats_available)
    except (TypeError, ValueError):
        return booking.get("tickets") or 1

    current_tickets = parse_positive_int(booking.get("tickets"), 1)
    return max(current_tickets, min(10, seats_available))


def booking_contact_phone_value(booking):
    return (booking.get("contact_phone") or booking.get("phone") or "").strip()


def payment_status_for_booking(status: str) -> str:
    if status == "Cancelled":
        return "Cancelled"
    if status == "Pending":
        return "Pending"
    return "Paid"


def payment_status_for_cancellation(refund_amount) -> str:
    if refund_amount > 0:
        return "Refund Approved"
    return "Cancellation Charge"



def build_booking_receipt_pdf(booking):
    """Delegate PDF rendering to the receipt helper module."""
    return _build_booking_receipt_pdf(booking)


def default_login_redirect():
    if g.current_user and g.current_user.get("role") == ROLE_ADMIN:
        return url_for("admin_dashboard")
    return url_for("home")


def view_events():
    return redirect(url_for("events"))


def build_event_listing_filters(
    category="",
    q="",
    date_filter="",
    date_from="",
    date_to="",
    month="",
    price_filter="",
):
    price_filter = (price_filter or "").strip().lower()
    if price_filter not in {"", "free", "paid"}:
        price_filter = ""

    filters = []
    params = []

    if category:
        filters.append("LOWER(c.category_name) = LOWER(%s)")
        params.append(category)

    if q:
        like = f"%{q}%"
        filters.append(
            "(e.event_name LIKE %s OR e.location LIKE %s OR v.venue_name LIKE %s OR c.category_name LIKE %s)"
        )
        params.extend([like, like, like, like])

    if date_filter:
        filters.append("DATE(e.event_date) = %s")
        params.append(date_filter)
    else:
        if month:
            filters.append("DATE_FORMAT(e.event_date, '%Y-%m') = %s")
            params.append(month)

        if date_from:
            filters.append("DATE(e.event_date) >= %s")
            params.append(date_from)

        if date_to:
            filters.append("DATE(e.event_date) <= %s")
            params.append(date_to)

    if price_filter == "free":
        filters.append("(e.price IS NULL OR e.price = 0)")
    elif price_filter == "paid":
        filters.append("(e.price IS NOT NULL AND e.price > 0)")

    return filters, params, price_filter


def fetch_event_listing(
    category="",
    q="",
    date_filter="",
    date_from="",
    date_to="",
    month="",
    price_filter="",
    limit=None,
    offset=None,
):
    filters, params, price_filter = build_event_listing_filters(
        category=category,
        q=q,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        month=month,
        price_filter=price_filter,
    )
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    where_clause = " AND ".join(filters) if filters else "1=1"
    limit_clause = ""
    if limit is not None:
        limit_clause = f" LIMIT {int(limit)}"
        if offset is not None:
            limit_clause += f" OFFSET {int(offset)}"

    cursor.execute(
        f"""
        SELECT e.*, v.venue_name, c.category_name,
               COALESCE(bt.booked_tickets, 0) AS booked_tickets,
               CASE
                    WHEN e.event_capacity IS NULL THEN NULL
                    ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
               END AS remaining_seats
        FROM events e
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        {booking_totals_join()}
        WHERE {where_clause}
        ORDER BY e.event_id DESC
        {limit_clause}
        """,
        tuple(params),
    )

    events_rows = [enrich_booking_event(event) for event in cursor.fetchall()]

    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()

    cursor.close()
    conn.close()

    return events_rows, categories, price_filter


def count_event_listing(
    category="",
    q="",
    date_filter="",
    date_from="",
    date_to="",
    month="",
    price_filter="",
):
    filters, params, _ = build_event_listing_filters(
        category=category,
        q=q,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        month=month,
        price_filter=price_filter,
    )

    where_clause = " AND ".join(filters) if filters else "1=1"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM events e
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        {booking_totals_join()}
        WHERE {where_clause}
        """,
        tuple(params),
    )
    total_events = cursor.fetchone()["count"] or 0

    cursor.close()
    conn.close()
    return total_events


def booking_ticket_limit(event) -> int:
    remaining = event.get("remaining_seats")
    if remaining is None:
        return 10

    try:
        remaining = int(remaining)
    except (TypeError, ValueError):
        return 0

    return max(0, min(10, remaining))


def enrich_booking_event(event):
    if not event:
        return None

    booking_event = dict(event)
    booking_event["event_duration_days"] = event_duration_days(booking_event)
    booking_event["event_date_range_label"] = event_date_range_label(booking_event)
    booking_event["booking_open_date"] = (
        event_booking_open_date(booking_event).strftime("%B %d, %Y")
        if event_booking_open_date(booking_event)
        else ""
    )
    booking_event["booking_close_date"] = (
        event_booking_close_date(booking_event).strftime("%B %d, %Y")
        if event_booking_close_date(booking_event)
        else ""
    )
    booking_event["is_bookable"] = is_event_bookable(booking_event, current_date())
    booking_event["is_sold_out"] = (
        booking_event.get("remaining_seats") is not None
        and int(booking_event.get("remaining_seats") or 0) <= 0
    )
    booking_event["max_tickets"] = booking_ticket_limit(booking_event)
    booking_event["event_date_label"] = booking_event["event_date_range_label"]
    booking_event["remaining_label"] = (
        "Unlimited"
        if booking_event.get("remaining_seats") is None
        else str(int(booking_event.get("remaining_seats") or 0))
    )
    return booking_event


def fetch_bookable_events():
    events_rows, _, _ = fetch_event_listing()
    today = current_date()
    bookable_events = []

    for event in events_rows:
        event_date = event.get("event_date")
        if not event_date or event_date < today:
            continue

        if not is_event_bookable(event, today):
            continue

        remaining = event.get("remaining_seats")
        if remaining is not None:
            try:
                remaining = int(remaining)
            except (TypeError, ValueError):
                continue
            if remaining <= 0:
                continue

        bookable_events.append(enrich_booking_event(event))

    return bookable_events


def process_booking_submission(cursor, conn, event, event_id, invalid_redirect, pending_booking=None):
    booked_at = current_datetime()
    today = booked_at.date()
    tickets = parse_ticket_count(request.form.get("tickets"))
    booking_days = parse_booking_days(
        request.form.get("booking_days"),
        event_duration_days(event),
    )
    phone = request.form.get("phone", "").strip() or None
    is_student = "is_student" in request.form
    payment_method = request.form.get("payment_method", "card").strip().lower()

    if tickets is None:
        flash("Tickets must be between 1 and 10.", "error")
        return redirect(invalid_redirect)

    if booking_days is None:
        flash("Please choose a valid number of booking days.", "error")
        return redirect(invalid_redirect)

    if payment_method not in PAYMENT_METHODS:
        flash("Please choose a valid payment method.", "error")
        return redirect(invalid_redirect)

    if not is_event_bookable(event, today):
        flash("This event is not currently open for booking.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    remaining = event.get("remaining_seats")
    if remaining is not None and tickets > int(remaining):
        flash(f"Only {remaining} seat(s) remain for this event.", "error")
        return redirect(invalid_redirect)

    cursor.execute(
        f"""
        SELECT booking_id
        FROM bookings
        WHERE user_id=%s
          AND event_id=%s
          AND {ACTIVE_BOOKING_CONDITION}
        """,
        (g.current_user["user_id"], event_id),
    )
    existing_booking = cursor.fetchone()
    if existing_booking:
        flash(
            "You already have a booking for this event. View it in My Bookings.",
            "error",
        )
        return redirect(url_for("my_bookings"))

    pricing = calculate_booking_breakdown(
        event.get("price"),
        tickets,
        is_student,
        booking_days=booking_days,
        event_duration=event_duration_days(event),
        event_date=event.get("event_date"),
        booked_at=booked_at,
    )

    try:
        cursor.execute(
            """
            UPDATE users
            SET phone=%s
            WHERE user_id=%s
            """,
            (phone, g.current_user["user_id"]),
        )

        cursor.execute(
            """
            INSERT INTO bookings (
                user_id, event_id, waitlist_id, contact_phone, booking_date, tickets, booking_days, is_student,
                subtotal_amount, student_discount_amount, advance_discount_amount,
                discount_applied, cancellation_charge, refund_amount, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.current_user["user_id"],
                event_id,
                pending_booking.get("waitlist_id") if pending_booking else None,
                phone,
                today,
                tickets,
                booking_days,
                is_student,
                pricing["base_subtotal"],
                pricing["student_discount"],
                pricing["advance_discount"],
                pricing["discount_total"],
                Decimal("0.00"),
                Decimal("0.00"),
                "Confirmed",
                booked_at,
            ),
        )
        booking_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO payments (booking_id, amount, payment_method, payment_status, payment_date)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                booking_id,
                pricing["total"],
                payment_method,
                "Paid",
                booked_at,
            ),
        )

        conn.commit()
        flash("Booking successful. Your receipt is ready.", "success")
        return redirect(url_for("booking_receipt", booking_id=booking_id))
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        return redirect(invalid_redirect)


def prepare_payment_details_session(cursor, conn, event, event_id, invalid_redirect):
    booked_at = current_datetime()
    today = booked_at.date()
    tickets = parse_ticket_count(request.form.get("tickets"))
    event_duration = event_duration_days(event)
    booking_days = parse_booking_days(request.form.get("booking_days"), event_duration)
    phone = request.form.get("phone", "").strip()
    is_student = "is_student" in request.form
    payment_method = normalize_payment_method(request.form.get("payment_method"))

    if tickets is None:
        flash("Tickets must be between 1 and 10.", "error")
        return redirect(invalid_redirect)

    if booking_days is None:
        flash("Please choose a valid number of booking days.", "error")
        return redirect(invalid_redirect)

    if not payment_method:
        flash("Please choose a valid payment method.", "error")
        return redirect(invalid_redirect)

    if not is_event_bookable(event, today):
        flash("This event is not currently open for booking.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    if event.get("remaining_seats") is not None and int(event["remaining_seats"]) <= 0:
        flash("This event is fully booked. Join the waitlist instead.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    remaining = event.get("remaining_seats")
    if remaining is not None and tickets > int(remaining):
        flash(f"Only {remaining} seat(s) remain for this event.", "error")
        return redirect(invalid_redirect)

    cursor.execute(
        f"""
        SELECT booking_id
        FROM bookings
        WHERE user_id=%s
          AND event_id=%s
          AND {ACTIVE_BOOKING_CONDITION}
        """,
        (g.current_user["user_id"], event_id),
    )
    existing_booking = cursor.fetchone()
    if existing_booking:
        flash(
            "You already have a booking for this event. View it in My Bookings.",
            "error",
        )
        return redirect(url_for("my_bookings"))

    pricing = calculate_booking_breakdown(
        event.get("price"),
        tickets,
        is_student,
        booking_days=booking_days,
        event_duration=event_duration,
        event_date=event.get("event_date"),
        booked_at=booked_at,
    )

    session[PENDING_BOOKING_SESSION_KEY] = {
        "event_id": int(event_id),
        "return_url": invalid_redirect,
        "event_name": event.get("event_name") or "",
        "event_date": event["event_date"].isoformat() if event.get("event_date") else "",
        "event_end_date": event["event_end_date"].isoformat() if event.get("event_end_date") else "",
        "event_date_label": event_date_range_label(event),
        "venue_name": event.get("venue_name") or "",
        "location": event.get("location") or "",
        "conditions": event.get("conditions") or "",
        "price": str(to_money(event.get("price"))),
        "tickets": tickets,
        "booking_days": booking_days,
        "phone": phone,
        "is_student": bool(is_student),
        "payment_method": payment_method,
        "payment_method_label": payment_method_label(payment_method),
        "booking_date": booked_at.isoformat(timespec="seconds"),
        "subtotal": str(pricing["base_subtotal"]),
        "student_discount": str(pricing["student_discount"]),
        "advance_discount": str(pricing["advance_discount"]),
        "discount": str(pricing["discount_total"]),
        "total_amount": str(pricing["total"]),
        "advance_discount_rate": str(pricing["advance_discount_rate"]),
        "advance_days": pricing["advance_days"],
        "remaining_seats": event.get("remaining_seats"),
        "max_tickets": booking_ticket_limit(event),
        "event_duration_days": event_duration,
        "waitlist_id": None,
    }

    return redirect(url_for("payment_details"))


def create_booking_from_pending(cursor, conn, pending_booking, payment_method, payment_source):
    event_id = int(pending_booking.get("event_id") or 0)
    tickets = parse_positive_int(pending_booking.get("tickets"), 1)
    booking_days = parse_positive_int(pending_booking.get("booking_days"), 1)
    is_student = bool(pending_booking.get("is_student"))
    phone = (pending_booking.get("phone") or "").strip() or None
    return_url = pending_booking.get("return_url") or url_for("book_tickets")
    booked_at = current_datetime()
    today = booked_at.date()

    event = fetch_event(cursor, event_id)
    if not event:
        flash("The selected event could not be found. Please start again.", "error")
        return None, redirect(return_url)

    if not is_event_bookable(event, today):
        flash("This event is not currently open for booking.", "error")
        return None, redirect(url_for("event_detail", event_id=event_id))

    remaining = event.get("remaining_seats")
    if remaining is not None and tickets > int(remaining):
        flash(f"Only {remaining} seat(s) remain for this event.", "error")
        return None, redirect(return_url)

    cursor.execute(
        f"""
        SELECT booking_id
        FROM bookings
        WHERE user_id=%s
          AND event_id=%s
          AND {ACTIVE_BOOKING_CONDITION}
        """,
        (g.current_user["user_id"], event_id),
    )
    existing_booking = cursor.fetchone()
    if existing_booking:
        flash(
            "You already have a booking for this event. View it in My Bookings.",
            "error",
        )
        return None, redirect(url_for("my_bookings"))

    pricing = calculate_booking_breakdown(
        event.get("price"),
        tickets,
        is_student,
        booking_days=booking_days,
        event_duration=event_duration_days(event),
        event_date=event.get("event_date"),
        booked_at=booked_at,
    )

    try:
        cursor.execute(
            """
            UPDATE users
            SET phone=%s
            WHERE user_id=%s
            """,
            (phone, g.current_user["user_id"]),
        )

        cursor.execute(
            """
            INSERT INTO bookings (
                user_id, event_id, contact_phone, booking_date, tickets, booking_days, is_student,
                subtotal_amount, student_discount_amount, advance_discount_amount,
                discount_applied, cancellation_charge, refund_amount, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.current_user["user_id"],
                event_id,
                phone,
                today,
                tickets,
                booking_days,
                is_student,
                pricing["base_subtotal"],
                pricing["student_discount"],
                pricing["advance_discount"],
                pricing["discount_total"],
                Decimal("0.00"),
                Decimal("0.00"),
                "Confirmed",
                booked_at,
            ),
        )
        booking_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO payments (
                booking_id, amount, payment_method, payment_source, payment_status, payment_date
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                booking_id,
                pricing["total"],
                payment_method,
                payment_source,
                "Paid",
                booked_at,
            ),
        )

        waitlist_id = pending_booking.get("waitlist_id")
        if waitlist_id:
            cursor.execute(
                """
                UPDATE event_waitlist
                SET status=%s,
                    booking_id=%s,
                    updated_at=%s
                WHERE waitlist_id=%s
                """,
                (
                    WAITLIST_STATUS_CONVERTED,
                    booking_id,
                    booked_at,
                    waitlist_id,
                ),
            )

        conn.commit()
        return booking_id, None
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        return None, redirect(return_url)


@admin_required
def admin_events():
    q = request.args.get("q", "").strip()
    category_id_raw = request.args.get("category_id", "").strip()
    date_from_raw = request.args.get("date_from", "").strip()
    date_to_raw = request.args.get("date_to", "").strip()

    date_from = parse_event_date(date_from_raw)
    date_to = parse_event_date(date_to_raw)

    filters = []
    params = []

    if q:
        like = f"%{q}%"
        filters.append(
            "(e.event_name LIKE %s OR e.location LIKE %s OR v.venue_name LIKE %s OR c.category_name LIKE %s)"
        )
        params.extend([like, like, like, like])

    if category_id_raw:
        try:
            category_id = int(category_id_raw)
        except (TypeError, ValueError):
            category_id = None
        if category_id:
            filters.append("e.category_id = %s")
            params.append(category_id)

    if date_from is None and date_from_raw:
        flash("Invalid start date filter.", "error")
    elif date_from is not None:
        filters.append("e.event_date >= %s")
        params.append(date_from)

    if date_to is None and date_to_raw:
        flash("Invalid end date filter.", "error")
    elif date_to is not None:
        filters.append("e.event_date <= %s")
        params.append(date_to)

    where_clause = " AND ".join(filters) if filters else "1=1"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        f"""
        SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.location, e.price,
               e.event_cost, e.event_capacity, e.conditions,
               v.venue_name, c.category_name,
               COALESCE(bt.booked_tickets, 0) AS booked_tickets,
               CASE
                    WHEN e.event_capacity IS NULL THEN NULL
                    ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
               END AS remaining_seats
        FROM events e
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        {booking_totals_join()}
        WHERE {where_clause}
        ORDER BY e.event_id DESC, e.event_date DESC
        """,
        tuple(params),
    )
    events_rows = cursor.fetchall()

    cursor.execute(
        "SELECT category_id, category_name FROM categories ORDER BY category_name"
    )
    categories = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "admin/events.html",
        events=events_rows,
        categories=categories,
        q=q,
        category_id=category_id_raw,
        date_from=date_from_raw,
        date_to=date_to_raw,
    )


@admin_required
def reduce_event_price(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    summary = event_price_reduction_summary(cursor, event_id)

    if not summary:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if not summary["eligible"]:
        cursor.close()
        conn.close()
        flash(
            "This event does not currently meet the rule for a 25% price reduction.",
            "error",
        )
        return redirect(url_for("admin_events"))

    try:
        cursor.execute(
            "UPDATE events SET price=%s WHERE event_id=%s",
            (summary["reduced_price"], event_id),
        )
        conn.commit()
        flash(
            f"Event price reduced from £{summary['current_price']:.2f} to £{summary['reduced_price']:.2f}.",
            "success",
        )
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("admin_events"))


@admin_required
def add_event():
    event = {}
    if request.method == "POST":
        event_name = request.form.get("event_name", "").strip()
        location = request.form.get("location", "").strip()
        event_date = parse_event_date(request.form.get("event_date"))
        event_end_date = parse_event_date(request.form.get("event_end_date"))
        conditions = request.form.get("conditions", "").strip()
        price = parse_price(request.form.get("price"))
        event_cost = parse_price(request.form.get("event_cost"))
        capacity_raw = request.form.get("event_capacity", "").strip()
        event_capacity = parse_capacity(capacity_raw)
        venue_id_raw = request.form.get("venue_id")
        category_id_raw = request.form.get("category_id")
        image_file = request.files.get("image_file")
        image_file_name = secure_filename(image_file.filename or "") if image_file and image_file.filename else ""

        try:
            venue_id = int(venue_id_raw)
            category_id = int(category_id_raw)
        except (TypeError, ValueError):
            venue_id = None
            category_id = None

        event = {
            "event_name": event_name,
            "location": location,
            "event_date": event_date,
            "event_end_date": event_end_date or event_date,
            "conditions": conditions,
            "price": price,
            "event_cost": event_cost,
            "venue_id": venue_id,
            "category_id": category_id,
            "event_capacity": event_capacity,
        }

        today = current_date()
        if (
            not event_name
            or event_date is None
            or not location
            or price is None
            or event_cost is None
            or not venue_id
            or not category_id
        ):
            flash("Please fill in all required fields (valid date/price).", "error")
        elif capacity_raw and event_capacity is None:
            flash("Capacity must be a number between 1 and 10000.", "error")
        elif event_date < today:
            flash("Event date cannot be in the past.", "error")
        elif event_end_date and event_end_date < event_date:
            flash("Event end date cannot be before the start date.", "error")
        elif image_file_name and not is_allowed_event_image_filename(image_file_name):
            flash("Please upload a JPG, JPEG, PNG, GIF, or WebP image.", "error")
        else:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            saved_image_url = None
            try:
                if not venue_exists(cursor, venue_id) or not category_exists(
                    cursor, category_id
                ):
                    flash("Please select a valid venue and category.", "error")
                else:
                    cursor.execute(
                        """
                        SELECT event_id FROM events
                        WHERE event_name=%s AND event_date=%s AND venue_id=%s
                        """,
                        (event_name, event_date, venue_id),
                    )
                    duplicate = cursor.fetchone()
                    if duplicate:
                        flash(
                            "Duplicate event detected for the same date and venue.",
                            "error",
                        )
                    else:
                        if image_file_name:
                            saved_image_url = save_event_image_upload(image_file)

                        cursor.execute(
                            """
                            INSERT INTO events (
                                event_name, event_date, event_end_date, location, conditions, price,
                                event_cost, venue_id, category_id, event_capacity, image_url
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                event_name,
                                event_date,
                                event_end_date or event_date,
                                location,
                                conditions or None,
                                price,
                                event_cost,
                                venue_id,
                                category_id,
                                event_capacity,
                                saved_image_url,
                            ),
                        )
                        conn.commit()
                        flash("Event added successfully.", "success")
                        return redirect(url_for("admin_events"))
            except (OSError, ValueError) as err:
                conn.rollback()
                if saved_image_url:
                    remove_uploaded_event_image(saved_image_url)
                flash(str(err), "error")
            except mysql.connector.Error as err:
                conn.rollback()
                if saved_image_url:
                    remove_uploaded_event_image(saved_image_url)
                flash(f"Database error: {err}", "error")
            finally:
                cursor.close()
                conn.close()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venues, categories = fetch_venues_and_categories(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "event_form.html",
        page_title="Add Event",
        submit_label="Add Event",
        action_url=url_for("add_event"),
        event=event,
        venues=venues,
        categories=categories,
    )


@admin_required
def update_event(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    event = fetch_event(cursor, event_id)
    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        event_name = request.form.get("event_name", "").strip()
        location = request.form.get("location", "").strip()
        event_date = parse_event_date(request.form.get("event_date"))
        price = parse_price(request.form.get("price"))
        capacity_raw = request.form.get("event_capacity", "").strip()
        event_capacity = parse_capacity(capacity_raw)
        venue_id_raw = request.form.get("venue_id")
        category_id_raw = request.form.get("category_id")
        event_end_date = parse_event_date(request.form.get("event_end_date"))
        conditions = request.form.get("conditions", "").strip()
        event_cost = parse_price(request.form.get("event_cost"))
        image_file = request.files.get("image_file")
        image_file_name = secure_filename(image_file.filename or "") if image_file and image_file.filename else ""
        remove_image = request.form.get("remove_image") == "1"
        current_image_url = event.get("image_url")

        try:
            venue_id = int(venue_id_raw)
            category_id = int(category_id_raw)
        except (TypeError, ValueError):
            venue_id = None
            category_id = None

        today = current_date()
        if (
            not event_name
            or event_date is None
            or not location
            or price is None
            or event_cost is None
            or not venue_id
            or not category_id
        ):
            flash("Please fill in all required fields (valid date/price).", "error")
        elif capacity_raw and event_capacity is None:
            flash("Capacity must be a number between 1 and 10000.", "error")
        elif event_date < today and event_date != event.get("event_date"):
            flash("Event date cannot be in the past.", "error")
        elif event_end_date and event_end_date < event_date:
            flash("Event end date cannot be before the start date.", "error")
        elif image_file_name and not is_allowed_event_image_filename(image_file_name):
            flash("Please upload a JPG, JPEG, PNG, GIF, or WebP image.", "error")
        else:
            saved_image_url = None
            try:
                if not venue_exists(cursor, venue_id) or not category_exists(
                    cursor, category_id
                ):
                    flash("Please select a valid venue and category.", "error")
                else:
                    cursor.execute(
                        """
                        SELECT event_id FROM events
                        WHERE event_name=%s AND event_date=%s AND venue_id=%s AND event_id<>%s
                        """,
                        (event_name, event_date, venue_id, event_id),
                    )
                    duplicate = cursor.fetchone()
                    if duplicate:
                        flash(
                            "Duplicate event detected for the same date and venue.",
                            "error",
                        )
                    else:
                        if image_file_name:
                            saved_image_url = save_event_image_upload(image_file)

                        image_url = (
                            saved_image_url
                            if saved_image_url is not None
                            else (None if remove_image else current_image_url)
                        )
                        cursor.execute(
                            """
                            UPDATE events
                            SET event_name=%s, event_date=%s, event_end_date=%s, location=%s, conditions=%s,
                                price=%s, event_cost=%s, venue_id=%s, category_id=%s, event_capacity=%s, image_url=%s
                            WHERE event_id=%s
                            """,
                            (
                                event_name,
                                event_date,
                                event_end_date or event_date,
                                location,
                                conditions or None,
                                price,
                                event_cost,
                                venue_id,
                                category_id,
                                event_capacity,
                                image_url,
                                event_id,
                            ),
                        )
                        waitlist_offers = promote_waitlist_entries(cursor, conn, event_id)
                        conn.commit()
                        if current_image_url and current_image_url != image_url:
                            remove_uploaded_event_image(current_image_url)
                        success_message = "Event updated successfully."
                        if waitlist_offers:
                            success_message += f" {len(waitlist_offers)} waitlist offer(s) were issued for available seats."
                        flash(success_message, "success")
                        cursor.close()
                        conn.close()
                        return redirect(url_for("admin_events"))
            except (OSError, ValueError) as err:
                conn.rollback()
                if saved_image_url:
                    remove_uploaded_event_image(saved_image_url)
                flash(str(err), "error")
            except mysql.connector.Error as err:
                conn.rollback()
                if saved_image_url:
                    remove_uploaded_event_image(saved_image_url)
                flash(f"Database error: {err}", "error")

        event.update(
            {
                "event_name": event_name,
                "location": location,
                "event_date": event_date,
                "event_end_date": event_end_date or event_date,
                "conditions": conditions,
                "price": price,
                "event_cost": event_cost,
                "venue_id": venue_id,
                "category_id": category_id,
                "event_capacity": event_capacity,
            }
        )

    venues, categories = fetch_venues_and_categories(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "event_form.html",
        page_title="Update Event",
        submit_label="Update Event",
        action_url=url_for("update_event", event_id=event_id),
        event=event,
        venues=venues,
        categories=categories,
    )


@admin_required
def delete_event(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT e.event_id, e.event_name, e.event_date,
               v.venue_name, c.category_name
        FROM events e
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        WHERE e.event_id=%s
        """,
        (event_id,),
    )
    event = cursor.fetchone()

    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        try:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM bookings WHERE event_id=%s", (event_id,)
            )
            bookings_count = cursor.fetchone()["cnt"]
            if bookings_count:
                flash("Cannot delete: this event already has bookings.", "error")
                cursor.close()
                conn.close()
                return redirect(url_for("admin_events"))

            cursor.execute("DELETE FROM events WHERE event_id=%s", (event_id,))
            conn.commit()
            flash("Event deleted successfully.", "success")
            cursor.close()
            conn.close()
            return redirect(url_for("admin_events"))
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")

    cursor.close()
    conn.close()
    return render_template("event_delete.html", event=event)


def home():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS count FROM events")
    events_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(DISTINCT venue_id) AS count FROM venues")
    venues_count = cursor.fetchone()["count"]

    cursor.execute(
        f"SELECT COALESCE(SUM(tickets), 0) AS total FROM bookings WHERE {ACTIVE_BOOKING_CONDITION}"
    )
    total_tickets = cursor.fetchone()["total"] or 0

    cursor.execute("SELECT category_name FROM categories ORDER BY category_name")
    categories = cursor.fetchall()

    cursor.execute(
        f"""
        SELECT e.*, v.venue_name, c.category_name,
               COALESCE(bt.booked_tickets, 0) AS booked_tickets,
               CASE
                    WHEN e.event_capacity IS NULL THEN NULL
                    ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
               END AS remaining_seats
        FROM events e
        LEFT JOIN venues v ON e.venue_id = v.venue_id
        LEFT JOIN categories c ON e.category_id = c.category_id
        {booking_totals_join()}
        WHERE e.event_date >= CURDATE()
        ORDER BY e.event_date
        LIMIT 3
        """
    )
    featured_events = cursor.fetchall()

    review_page = parse_positive_int(request.args.get("review_page"), 1)
    reviews_per_page = 6
    approved_reviews_count = count_reviews(cursor, public_only=True)
    satisfaction_rate = calculate_satisfaction_rate(cursor)
    review_total_pages = max(1, (approved_reviews_count + reviews_per_page - 1) // reviews_per_page)
    review_page = min(review_page, review_total_pages)
    review_offset = (review_page - 1) * reviews_per_page
    reviews = fetch_reviews(
        cursor,
        public_only=True,
        limit=reviews_per_page,
        offset=review_offset,
    )
    review_pagination_links = []
    for item in build_pagination_pages(review_page, review_total_pages):
        if item is None:
            review_pagination_links.append({"type": "ellipsis"})
        else:
            review_pagination_links.append(
                {
                    "type": "page",
                    "page": item,
                    "url": url_for("home", review_page=item),
                }
            )

    cursor.close()
    conn.close()

    return render_template(
        "index.html",
        events_count=events_count,
        venues_count=venues_count,
        attendees_count=total_tickets,
        categories=categories,
        featured_events=featured_events,
        reviews=reviews,
        review_page=review_page,
        review_total_pages=review_total_pages,
        review_total_count=approved_reviews_count,
        review_start_item=((review_page - 1) * reviews_per_page + 1) if approved_reviews_count else 0,
        review_end_item=min(review_page * reviews_per_page, approved_reviews_count) if approved_reviews_count else 0,
        review_pagination_links=review_pagination_links,
        satisfaction_rate=satisfaction_rate,
    )


def subscribe():
    email = request.form.get("email", "").strip().lower()
    next_url = url_for("home") + "#newsletter"

    if not email:
        flash("Please enter your email address.", "error")
        return redirect(next_url)

    if not is_valid_email(email):
        flash("Please enter a valid email address.", "error")
        return redirect(next_url)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT subscriber_id
            FROM newsletter_subscribers
            WHERE email = %s
            LIMIT 1
            """,
            (email,),
        )
        existing_subscriber = cursor.fetchone()
        if existing_subscriber:
            flash("This email is already subscribed.", "success")
            return redirect(next_url)

        cursor.execute(
            """
            INSERT INTO newsletter_subscribers (email, created_at)
            VALUES (%s, %s)
            """,
            (email, current_datetime()),
        )
        conn.commit()
        flash("Thanks for subscribing. We'll keep you updated.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(next_url)


def submit_review():
    if request.method == "GET":
        return redirect(url_for("home"))

    if not g.current_user:
        flash("Please log in to share a review.", "error")
        return redirect(url_for("login", next=url_for("account")))

    review_content = request.form.get("content", "").strip()
    rating_raw = request.form.get("rating", "").strip()

    if not review_content:
        flash("Please write a review before submitting.", "error")
        return redirect(url_for("home"))

    if len(review_content) > 1000:
        flash("Reviews must be 1000 characters or fewer.", "error")
        return redirect(url_for("home"))

    try:
        rating = int(rating_raw)
    except (TypeError, ValueError):
        rating = 0

    if rating < 1 or rating > 5:
        flash("Please choose a rating between 1 and 5 stars.", "error")
        return redirect(url_for("home"))

    author_name = (g.current_user.get("full_name") or "Community Guest").strip() or "Community Guest"
    author_initials = build_initials(author_name)
    now = current_datetime()

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO reviews (
                user_id, author_name, author_initials, rating, content, status,
                created_at, updated_at, reviewed_by, reviewed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.current_user["user_id"],
                author_name[:255],
                author_initials[:10],
                rating,
                review_content,
                REVIEW_STATUS_PENDING,
                now,
                now,
                None,
                None,
            ),
        )
        conn.commit()
        flash("Your review has been submitted and is pending approval.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("account"))


def events():
    category = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()
    date_filter = request.args.get("date", "").strip()
    date_from_raw = request.args.get("date_from", "").strip()
    date_to_raw = request.args.get("date_to", "").strip()
    month_raw = request.args.get("month", "").strip()
    price_filter = request.args.get("price", "").strip().lower()
    page = parse_positive_int(request.args.get("page"), 1)
    per_page = 6

    date_from = parse_event_date(date_from_raw)
    date_to = parse_event_date(date_to_raw)
    month = ""
    if month_raw:
        if re.fullmatch(r"\d{4}-\d{2}", month_raw):
            month = month_raw
        else:
            flash("Invalid month filter.", "error")

    total_events = count_event_listing(
        category=category,
        q=q,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        month=month,
        price_filter=price_filter,
    )
    total_pages = max(1, (total_events + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    events_rows, categories, price_filter = fetch_event_listing(
        category=category,
        q=q,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        month=month,
        price_filter=price_filter,
        limit=per_page,
        offset=offset,
    )
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venues = fetch_venue_overview(cursor)

    pagination_base_args = {}
    if category:
        pagination_base_args["category"] = category
    if q:
        pagination_base_args["q"] = q
    if date_filter:
        pagination_base_args["date"] = date_filter
    if date_from_raw and not date_filter:
        pagination_base_args["date_from"] = date_from_raw
    if date_to_raw and not date_filter:
        pagination_base_args["date_to"] = date_to_raw
    if month and not date_filter:
        pagination_base_args["month"] = month
    if price_filter:
        pagination_base_args["price"] = price_filter

    pagination_links = []
    for item in build_pagination_pages(page, total_pages):
        if item is None:
            pagination_links.append({"type": "ellipsis"})
        else:
            page_args = dict(pagination_base_args)
            page_args["page"] = item
            pagination_links.append(
                {
                    "type": "page",
                    "page": item,
                    "url": url_for("events", **page_args),
                }
            )

    cursor.close()
    conn.close()

    return render_template(
        "events.html",
        events=events_rows,
        venues=venues,
        categories=categories,
        q=q,
        category=category,
        date=date_filter,
        date_from=date_from_raw,
        date_to=date_to_raw,
        month=month,
        price=price_filter,
        page=page,
        total_pages=total_pages,
        total_events=total_events,
        start_item=((page - 1) * per_page + 1) if total_events else 0,
        end_item=min(page * per_page, total_events) if total_events else 0,
        pagination_links=pagination_links,
        pagination_prev_url=url_for("events", page=page - 1, **pagination_base_args) if page > 1 else None,
        pagination_next_url=url_for("events", page=page + 1, **pagination_base_args) if page < total_pages else None,
    )


def venues_page():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venues = fetch_venue_overview(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "venues.html",
        venues=venues,
        total_venues=len(venues),
    )


def venue_detail(venue_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venue, venue_events = fetch_venue_details(cursor, venue_id)
    cursor.close()
    conn.close()

    if not venue:
        return render_template("404.html"), 404

    return render_template(
        "venue_detail.html",
        venue=venue,
        venue_events=venue_events,
    )


@login_required
def book_tickets():
    if request.method == "POST":
        event_id_raw = request.form.get("event_id", "").strip()
        try:
            event_id = int(event_id_raw)
        except (TypeError, ValueError):
            flash("Please select an event to book.", "error")
            return redirect(url_for("book_tickets"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        event = fetch_event(cursor, event_id)

        if not event:
            cursor.close()
            conn.close()
            flash("Please choose a valid event to book.", "error")
            return redirect(url_for("book_tickets"))

        response = prepare_payment_details_session(
            cursor,
            conn,
            enrich_booking_event(event),
            event_id,
            url_for("book_tickets"),
        )
        cursor.close()
        conn.close()
        return response

    return render_template(
        "booking.html",
        event=None,
        booking_events=fetch_bookable_events(),
        today=current_date(),
        booking_now=current_datetime(),
    )


def event_detail(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    event = fetch_event(cursor, event_id)
    cursor.close()
    conn.close()

    if not event:
        return render_template("404.html"), 404

    return render_template("event_details.html", event=enrich_booking_event(event))


@login_required
def book(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    event = fetch_event(cursor, event_id)
    booking_event = enrich_booking_event(event)

    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        response = prepare_payment_details_session(
            cursor,
            conn,
            booking_event,
            event_id,
            url_for("book", event_id=event_id),
        )
        cursor.close()
        conn.close()
        return response

    if booking_event and not booking_event["is_bookable"] and not booking_event["is_sold_out"]:
        cursor.close()
        conn.close()
        flash("Bookings for this event are not open yet.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    cursor.close()
    conn.close()
    return render_template(
        "booking.html",
        event=booking_event,
        booking_events=[],
        today=current_date(),
        booking_now=current_datetime(),
    )


@login_required
def join_waitlist(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    event = fetch_event(cursor, event_id)
    booking_event = enrich_booking_event(event)

    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method != "POST":
        cursor.close()
        conn.close()
        return redirect(url_for("event_detail", event_id=event_id))

    if not booking_event or not booking_event["is_sold_out"]:
        cursor.close()
        conn.close()
        flash("This event is not full yet, so you can book it directly.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    tickets = parse_ticket_count(request.form.get("tickets")) or 1
    booking_days = parse_booking_days(
        request.form.get("booking_days"),
        event_duration_days(event),
    ) or 1

    cursor.execute(
        """
        SELECT waitlist_id
        FROM event_waitlist
        WHERE event_id=%s
          AND user_id=%s
          AND status IN (%s, %s)
        LIMIT 1
        """,
        (
            event_id,
            g.current_user["user_id"],
            WAITLIST_STATUS_WAITING,
            WAITLIST_STATUS_OFFERED,
        ),
    )
    existing_waitlist = cursor.fetchone()
    if existing_waitlist:
        cursor.close()
        conn.close()
        flash("You already have an active waitlist request for this event.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    try:
        cursor.execute(
            """
            INSERT INTO event_waitlist (
                event_id, user_id, requested_tickets, booking_days, status, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                g.current_user["user_id"],
                tickets,
                booking_days,
                WAITLIST_STATUS_WAITING,
                current_datetime(),
                current_datetime(),
            ),
        )
        conn.commit()
        flash("You have joined the waitlist. We'll offer the place when a seat opens up.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("event_detail", event_id=event_id))


@login_required
def accept_waitlist_offer(waitlist_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    waitlist_entry = fetch_waitlist_entry(cursor, waitlist_id)

    if not waitlist_entry:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if waitlist_entry.get("user_id") != g.current_user.get("user_id"):
        cursor.close()
        conn.close()
        abort(403)

    if not waitlist_offer_is_active(waitlist_entry):
        cursor.execute(
            """
            UPDATE event_waitlist
            SET status=%s,
                updated_at=%s
            WHERE waitlist_id=%s
            """,
            (WAITLIST_STATUS_EXPIRED, current_datetime(), waitlist_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("That waitlist offer has expired.", "error")
        return redirect(url_for("account"))

    event = fetch_event(cursor, waitlist_entry["event_id"])
    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    requested_tickets = int(waitlist_entry.get("requested_tickets") or 1)
    if event.get("remaining_seats") is not None and int(event["remaining_seats"]) < requested_tickets:
        cursor.execute(
            """
            UPDATE event_waitlist
            SET status=%s,
                updated_at=%s
            WHERE waitlist_id=%s
            """,
            (WAITLIST_STATUS_EXPIRED, current_datetime(), waitlist_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("The offered place is no longer available.", "error")
        return redirect(url_for("event_detail", event_id=event["event_id"]))

    pending_booking = build_pending_booking_from_waitlist(
        waitlist_entry,
        enrich_booking_event(event),
    )
    session[PENDING_BOOKING_SESSION_KEY] = pending_booking
    cursor.close()
    conn.close()
    flash("Your waitlist offer is ready. Complete payment to confirm the booking.", "success")
    return redirect(url_for("payment_details"))


@login_required
def reject_waitlist_offer(waitlist_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    waitlist_entry = fetch_waitlist_entry(cursor, waitlist_id)

    if not waitlist_entry:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if waitlist_entry.get("user_id") != g.current_user.get("user_id"):
        cursor.close()
        conn.close()
        abort(403)

    event = fetch_event(cursor, waitlist_entry["event_id"])
    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    was_active_offer = waitlist_offer_is_active(waitlist_entry)
    new_status = WAITLIST_STATUS_CANCELLED if was_active_offer else WAITLIST_STATUS_EXPIRED

    cursor.execute(
        """
        UPDATE event_waitlist
        SET status=%s,
            offer_expires_at=NULL,
            updated_at=%s
        WHERE waitlist_id=%s
        """,
        (new_status, current_datetime(), waitlist_id),
    )
    promote_waitlist_entries(cursor, conn, event["event_id"])
    conn.commit()
    cursor.close()
    conn.close()

    if was_active_offer:
        flash("Your waitlist offer was rejected and the next person in line has been offered the seat.", "success")
    else:
        flash("That waitlist offer had already expired, and the queue has been moved forward.", "success")
    return redirect(url_for("account"))


@login_required
def payment_details():
    pending_booking = session.get(PENDING_BOOKING_SESSION_KEY)
    if not pending_booking:
        flash("Please start a booking before entering payment details.", "error")
        return redirect(url_for("book_tickets"))

    pending_payment_method = normalize_payment_method(pending_booking.get("payment_method"))
    if not pending_payment_method:
        flash("Your payment session is missing a valid payment method.", "error")
        session.pop(PENDING_BOOKING_SESSION_KEY, None)
        return redirect(url_for("book_tickets"))

    if request.method == "GET":
        return render_template(
            "payment_details.html",
            pending_booking=pending_booking,
            payment_method=pending_payment_method,
            payment_method_label=payment_method_label(pending_payment_method),
            payment_methods=PAYMENT_METHOD_LABELS,
            field_errors={},
            form_values={},
        )

    payment_method = normalize_payment_method(
        request.form.get("payment_method") or pending_payment_method
    )
    field_errors, cleaned_values = validate_payment_details(payment_method, request.form)
    if field_errors:
        flash("Please correct the payment details below.", "error")
        return render_template(
            "payment_details.html",
            pending_booking=pending_booking,
            payment_method=payment_method or pending_payment_method,
            payment_method_label=payment_method_label(payment_method or pending_payment_method),
            payment_methods=PAYMENT_METHOD_LABELS,
            field_errors=field_errors,
            form_values=request.form,
        )

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        booking_id, error_redirect = create_booking_from_pending(
            cursor,
            conn,
            pending_booking,
            payment_method,
            build_payment_source_summary(payment_method, cleaned_values),
        )
        if booking_id is None:
            session.pop(PENDING_BOOKING_SESSION_KEY, None)
            cursor.close()
            conn.close()
            return error_redirect or redirect(url_for("book_tickets"))

        session.pop(PENDING_BOOKING_SESSION_KEY, None)
        flash("Payment details submitted successfully. Your booking is confirmed.", "success")
        cursor.close()
        conn.close()
        return redirect(url_for("booking_receipt", booking_id=booking_id))
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        cursor.close()
        conn.close()
        return render_template(
            "payment_details.html",
            pending_booking=pending_booking,
            payment_method=payment_method,
            payment_method_label=payment_method_label(payment_method),
            payment_methods=PAYMENT_METHOD_LABELS,
            field_errors={},
            form_values=request.form,
        )


@login_required
def bookings_list():
    return redirect(url_for("my_bookings"))


@login_required
def my_bookings():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booked_at_sql = booking_booked_at_sql()
    cursor.execute(
        f"""
        SELECT b.booking_id, b.booking_date, b.created_at, {booked_at_sql} AS booked_at,
               b.tickets, b.booking_days, b.is_student,
               b.contact_phone,
               b.subtotal_amount, b.student_discount_amount, b.advance_discount_amount,
               b.discount_applied, b.cancellation_charge, b.refund_amount,
               COALESCE(b.status, 'Confirmed') AS status,
               e.event_id, e.event_name, e.event_date, e.event_end_date, e.location,
               p.amount, p.payment_method, p.payment_source, COALESCE(p.payment_status, 'Pending') AS payment_status,
               p.payment_date
        FROM bookings b
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        WHERE b.user_id = %s
        ORDER BY booked_at DESC, b.booking_id DESC
        """,
        (g.current_user["user_id"],),
    )
    bookings = cursor.fetchall()
    for booking in bookings:
        booking["receipt_reference"] = booking_receipt_reference(booking["booking_id"])
        booking["can_edit"] = booking_is_user_editable(booking)
    cursor.close()
    conn.close()
    return render_template("my_bookings.html", bookings=bookings)


def delete_user_account(cursor, user_id: int):
    cursor.execute(
        """
        SELECT DISTINCT event_id
        FROM bookings
        WHERE user_id = %s
          AND COALESCE(status, 'Confirmed') <> 'Cancelled'
        """,
        (user_id,),
    )
    active_event_ids = [row["event_id"] for row in cursor.fetchall()]

    cursor.execute(
        """
        UPDATE reviews
        SET user_id = NULL,
            author_name = %s,
            author_initials = %s
        WHERE user_id = %s
        """,
        ("Deleted user", "DU", user_id),
    )
    cursor.execute(
        """
        DELETE p
        FROM payments p
        INNER JOIN bookings b ON b.booking_id = p.booking_id
        WHERE b.user_id = %s
        """,
        (user_id,),
    )
    cursor.execute("DELETE FROM bookings WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
    return active_event_ids


@login_required
def account():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT user_id, full_name, email, phone, role, created_at, password_hash, password_changed_at
        FROM users
        WHERE user_id = %s
        """,
        (g.current_user["user_id"],),
    )
    account_user = cursor.fetchone()

    if not account_user:
        cursor.close()
        conn.close()
        session.clear()
        flash("Your account could not be found. Please log in again.", "error")
        return redirect(url_for("login"))

    password_hash = account_user.pop("password_hash") or ""

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        try:
            if action == "profile":
                full_name = request.form.get("full_name", "").strip()
                phone = request.form.get("phone", "").strip()

                if not full_name:
                    flash("Name cannot be empty.", "error")
                else:
                    cursor.execute(
                        """
                        UPDATE users
                        SET full_name=%s, phone=%s
                        WHERE user_id=%s
                        """,
                        (full_name, phone, account_user["user_id"]),
                    )
                    conn.commit()
                    flash("Profile updated successfully.", "success")
                    cursor.close()
                    conn.close()
                    return redirect(url_for("account"))
            elif action == "password":
                current_password = request.form.get("current_password", "").strip()
                new_password = request.form.get("new_password", "").strip()
                confirm_password = request.form.get("confirm_password", "").strip()

                if not current_password or not new_password or not confirm_password:
                    flash("Please fill in all password fields.", "error")
                elif not check_password_hash(password_hash, current_password):
                    flash("Current password is incorrect.", "error")
                elif len(new_password) < 8:
                    flash("New password must be at least 8 characters long.", "error")
                elif new_password != confirm_password:
                    flash("Passwords do not match.", "error")
                else:
                    password_changed_at = current_datetime()
                    cursor.execute(
                        """
                        UPDATE users
                        SET password_hash=%s, password_changed_at=%s
                        WHERE user_id=%s
                        """,
                        (generate_password_hash(new_password), password_changed_at, account_user["user_id"]),
                    )
                    invalidate_password_reset_tokens(cursor, account_user["user_id"])
                    conn.commit()
                    session[PASSWORD_VERSION_SESSION_KEY] = serialize_password_version(password_changed_at)
                    flash("Password changed successfully.", "success")
                    cursor.close()
                    conn.close()
                    return redirect(url_for("account"))
            elif action == "delete_account":
                delete_password = request.form.get("delete_password", "").strip()

                if not delete_password:
                    flash("Please enter your current password to delete your account.", "error")
                elif not password_hash or not check_password_hash(password_hash, delete_password):
                    flash("Current password is incorrect.", "error")
                else:
                    try:
                        active_event_ids = delete_user_account(cursor, account_user["user_id"])
                        for event_id in active_event_ids:
                            promote_waitlist_entries(cursor, conn, event_id)
                        conn.commit()
                        cursor.close()
                        conn.close()
                        session.clear()
                        flash("Your account has been deleted.", "success")
                        return redirect(url_for("login"))
                    except mysql.connector.Error as err:
                        conn.rollback()
                        flash(f"Database error: {err}", "error")
            else:
                flash("Invalid account action.", "error")
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")

    contact_messages = fetch_contact_messages(
        cursor,
        user_id=account_user["user_id"],
        include_user_deleted=False,
    )
    user_reviews = fetch_reviews(
        cursor,
        user_id=account_user["user_id"],
        order_by="r.created_at DESC, r.review_id DESC",
    )
    waitlist_entries = fetch_waitlist_entries(
        cursor,
        user_id=account_user["user_id"],
        order_by="w.created_at DESC, w.waitlist_id DESC",
    )

    cursor.close()
    conn.close()

    return render_template(
        "account.html",
        account_user=account_user,
        contact_messages=contact_messages,
        user_reviews=user_reviews,
        waitlist_entries=waitlist_entries,
        now=current_datetime(),
    )


@login_required
def account_delete_contact_message(message_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE contact_messages
            SET user_deleted_at=%s
            WHERE message_id=%s AND user_id=%s AND user_deleted_at IS NULL
            """,
            (current_datetime(), message_id, g.current_user["user_id"]),
        )

        if cursor.rowcount == 0:
            flash("Message not found or already removed from your profile.", "error")
        else:
            conn.commit()
            flash(
                "Message removed from your profile. Admins can still see it until they delete it.",
                "success",
            )
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("account"))


@login_required
def account_edit_review(review_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    review_rows = fetch_reviews(
        cursor,
        review_id=review_id,
        user_id=g.current_user["user_id"],
        limit=1,
    )

    if not review_rows:
        cursor.close()
        conn.close()
        flash("Review not found.", "error")
        return redirect(url_for("account"))

    review = review_rows[0]

    if request.method == "POST":
        rating_raw = request.form.get("rating", "").strip()
        content = request.form.get("content", "").strip()

        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            rating = 0

        if rating < 1 or rating > 5:
            flash("Please choose a rating between 1 and 5 stars.", "error")
        elif not content:
            flash("Please enter your review text.", "error")
        elif len(content) > 1000:
            flash("Reviews must be 1000 characters or fewer.", "error")
        else:
            now = current_datetime()
            try:
                cursor.execute(
                    """
                    UPDATE reviews
                    SET author_name=%s,
                        author_initials=%s,
                        rating=%s,
                        content=%s,
                        status=%s,
                        updated_at=%s,
                        reviewed_by=NULL,
                        reviewed_at=NULL
                    WHERE review_id=%s AND user_id=%s
                    """,
                    (
                        (g.current_user.get("full_name") or "Community Guest").strip() or "Community Guest",
                        build_initials(g.current_user.get("full_name") or "Community Guest")[:10],
                        rating,
                        content,
                        REVIEW_STATUS_PENDING,
                        now,
                        review_id,
                        g.current_user["user_id"],
                    ),
                )
                conn.commit()
                flash("Review updated and sent back for approval.", "success")
                cursor.close()
                conn.close()
                return redirect(url_for("account"))
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")

        review["rating"] = rating if 1 <= rating <= 5 else review["rating"]
        review["content"] = content or review["content"]

    cursor.close()
    conn.close()
    return render_template("review_form.html", review=review, next_url=url_for("account"))


@login_required
def account_delete_review(review_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM reviews WHERE review_id=%s AND user_id=%s",
            (review_id, g.current_user["user_id"]),
        )

        if cursor.rowcount == 0:
            flash("Review not found or you do not have permission to delete it.", "error")
        else:
            conn.commit()
            flash("Review deleted.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("account"))


@login_required
def booking_receipt(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booking = fetch_booking_details(cursor, booking_id)
    cursor.close()
    conn.close()

    if not booking:
        return render_template("404.html"), 404
    if not can_access_booking(booking):
        abort(403)

    booking["can_edit"] = booking_is_user_editable(booking)

    return render_template(
        "booking_receipt.html",
        booking=booking,
        receipt_reference=booking_receipt_reference(booking_id),
    )


@login_required
def download_booking_receipt(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booking = fetch_booking_details(cursor, booking_id)
    cursor.close()
    conn.close()

    if not booking:
        return render_template("404.html"), 404
    if not can_access_booking(booking):
        abort(403)

    content = build_booking_receipt_pdf(booking)
    receipt_reference = booking_receipt_reference(booking_id)

    return Response(
        content,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={receipt_reference.lower()}-receipt.pdf"
        },
    )


def describe_refund_account(booking):
    payment_source = (booking.get("payment_source") or "").strip()
    if payment_source:
        return payment_source

    payment_method = normalize_payment_method(booking.get("payment_method"))
    if payment_method:
        return f"The same {payment_method_label(payment_method)} account used for this booking"

    return "The original payment account used for this booking"


def refund_status_context(booking, outcome=""):
    free_cancellation_deadline = refund_deadline_for_event(booking.get("event_date"))
    free_cancellation_deadline_label = (
        free_cancellation_deadline.strftime("%B %d, %Y") if free_cancellation_deadline else ""
    )

    preview = calculate_cancellation_charge(
        booking.get("amount") or booking.get("payment_amount") or 0,
        booking.get("event_date"),
    )
    if (booking.get("status") or "").strip() == "Cancelled":
        cancellation_charge = to_money(booking.get("cancellation_charge"))
        refund_amount = to_money(booking.get("refund_amount"))
    else:
        cancellation_charge = preview["cancellation_charge"]
        refund_amount = preview["refund_amount"]

    refund_allowed, _ = refund_is_allowed(booking.get("event_date"))
    refund_heading = ""
    refund_message = ""

    if outcome == "cancelled":
        refund_heading = "Booking cancelled"
        if refund_amount > 0:
            refund_message = (
                f"Cancellation charge: £{cancellation_charge:.2f}. "
                f"Refund amount: £{refund_amount:.2f}. "
                f"The refund will be processed within {REFUND_PROCESSING_WORKING_DAYS} working days."
            )
        else:
            refund_message = (
                f"Cancellation charge: £{cancellation_charge:.2f}. "
                "No refund is due for this booking."
            )
    elif outcome == "already_cancelled":
        refund_heading = "Booking already cancelled"
        refund_message = "This booking was already cancelled before this refund check."
    else:
        refund_heading = "Cancellation policy"
        refund_message = (
            "Cancellations made 40 or more days before the event are free. "
            "Cancellations between 25 and 39 days before the event incur a 40% charge. "
            "Cancellations within 25 days of the event incur a 100% charge."
        )

    return {
        "refund_allowed": refund_allowed,
        "refund_deadline": free_cancellation_deadline,
        "refund_deadline_label": free_cancellation_deadline_label,
        "refund_heading": refund_heading,
        "refund_message": refund_message,
        "refund_account": describe_refund_account(booking),
        "refund_processing_days": REFUND_PROCESSING_WORKING_DAYS,
        "refund_window_hours": REFUND_WINDOW_HOURS,
        "cancellation_charge": cancellation_charge,
        "refund_amount": refund_amount,
    }


def refund_policy():
    return render_template(
        "refund_policy.html",
        booking=None,
        refund_outcome="",
        refund_heading="Refund Policy",
        refund_message=(
            "Cancellations made 40 or more days before the event are free. "
            "Between 25 and 39 days before the event, a 40% charge applies. "
            "Within 25 days, the cancellation charge is 100% of the booking price."
        ),
        refund_deadline=None,
        refund_deadline_label="",
        refund_account="",
        refund_processing_days=REFUND_PROCESSING_WORKING_DAYS,
        refund_window_hours=REFUND_WINDOW_HOURS,
        refund_allowed=False,
    )


@login_required
def booking_refund(booking_id):
    outcome = (request.args.get("outcome") or "").strip().lower()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booking = fetch_booking_details(cursor, booking_id)
    cursor.close()
    conn.close()

    if not booking:
        return render_template("404.html"), 404
    if not can_access_booking(booking):
        abort(403)

    if not outcome:
        booking_status = (booking.get("status") or "").strip()
        if booking_status == "Cancelled":
            outcome = "cancelled"
        else:
            free_cancellation_deadline = refund_deadline_for_event(booking.get("event_date"))
            if free_cancellation_deadline and current_date() > free_cancellation_deadline:
                outcome = "policy"
            elif free_cancellation_deadline:
                outcome = "policy"
            else:
                outcome = "policy"

    context = refund_status_context(booking, outcome)
    if not context["refund_heading"]:
        context["refund_heading"] = "Refund Policy"
        context["refund_message"] = (
            "Cancellations made 40 or more days before the event are free. "
            "Between 25 and 39 days before the event, a 40% charge applies. "
            "Within 25 days, the cancellation charge is 100% of the booking price."
        )

    return render_template(
        "refund_policy.html",
        booking=booking,
        refund_outcome=outcome,
        **context,
    )


@login_required
def cancel_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booking = fetch_booking_details(cursor, booking_id)

    if not booking:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if not can_access_booking(booking):
        cursor.close()
        conn.close()
        abort(403)

    if booking.get("status") == "Cancelled":
        cursor.close()
        conn.close()
        flash("This booking is already cancelled.", "error")
        return redirect(url_for("booking_refund", booking_id=booking_id, outcome="already_cancelled"))

    try:
        cancellation = calculate_cancellation_charge(
            booking.get("amount") or 0,
            booking.get("event_date"),
        )
        cancellation_charge = cancellation["cancellation_charge"]
        refund_amount = cancellation["refund_amount"]
        payment_status = payment_status_for_cancellation(refund_amount)

        cursor.execute(
            """
            UPDATE bookings
            SET status=%s,
                cancellation_charge=%s,
                refund_amount=%s
            WHERE booking_id=%s
            """,
            (
                "Cancelled",
                cancellation_charge,
                refund_amount,
                booking_id,
            ),
        )
        cursor.execute(
            """
            UPDATE payments
            SET payment_status=%s
            WHERE booking_id=%s
            """,
            (payment_status, booking_id),
        )
        promote_waitlist_entries(cursor, conn, booking["event_id"])
        conn.commit()
        if refund_amount > 0:
            flash(
                f"Booking cancelled. £{cancellation_charge:.2f} cancellation charge applied and £{refund_amount:.2f} will be refunded.",
                "success",
            )
        else:
            flash(
                f"Booking cancelled. The full booking amount is retained as a cancellation charge.",
                "success",
            )
        cursor.close()
        conn.close()
        return redirect(url_for("booking_refund", booking_id=booking_id, outcome="cancelled"))
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        cursor.close()
        conn.close()
        return redirect(url_for("my_bookings"))


def admin_login():
    next_url = get_safe_next_url() or url_for("admin_dashboard")
    return redirect(url_for("login", next=next_url))


def admin_logout():
    return redirect(url_for("logout"))


@admin_required
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booked_at_sql = booking_booked_at_sql()

    cursor.execute("SELECT COUNT(*) AS count FROM events")
    events_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM venues")
    venues_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM users")
    users_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) AS count FROM bookings")
    bookings_count = cursor.fetchone()["count"]

    cursor.execute(
        f"SELECT COALESCE(SUM(tickets), 0) AS total FROM bookings WHERE {ACTIVE_BOOKING_CONDITION}"
    )
    tickets_total = cursor.fetchone()["total"] or 0

    cursor.execute(
        f"""
        SELECT {booking_revenue_total_sql()} AS total
        FROM bookings b
        """
    )
    revenue_total = cursor.fetchone()["total"] or 0

    cursor.execute(
        """
        SELECT COALESCE(SUM(COALESCE(event_cost, 0)), 0) AS total
        FROM events
        """
    )
    cost_total = cursor.fetchone()["total"] or 0
    profit_total = to_money(revenue_total) - to_money(cost_total)

    messages_count = count_contact_messages(cursor)
    new_messages_count = count_contact_messages(cursor, status="New")
    reviews_count = count_reviews(cursor)
    pending_reviews_count = count_reviews(cursor, status=REVIEW_STATUS_PENDING)

    cursor.execute(
        f"""
        SELECT b.booking_id, b.booking_date, b.created_at, {booked_at_sql} AS booked_at, b.tickets,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_name, e.event_date,
               COALESCE(p.payment_status, 'Pending') AS payment_status
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        ORDER BY booked_at DESC, b.booking_id DESC
        LIMIT 5
        """
    )
    recent_bookings = cursor.fetchall()

    recent_messages = fetch_contact_messages(cursor, limit=5)
    recent_reviews = fetch_reviews(cursor, limit=5)

    cursor.close()
    conn.close()

    return render_template(
        "admin/dashboard.html",
        events_count=events_count,
        venues_count=venues_count,
        users_count=users_count,
        bookings_count=bookings_count,
        tickets_total=tickets_total,
        revenue_total=revenue_total,
        cost_total=cost_total,
        profit_total=profit_total,
        recent_bookings=recent_bookings,
        messages_count=messages_count,
        new_messages_count=new_messages_count,
        recent_messages=recent_messages,
        reviews_count=reviews_count,
        pending_reviews_count=pending_reviews_count,
        recent_reviews=recent_reviews,
    )


@admin_required
def admin_reports():
    event_id_raw = request.args.get("event_id", "").strip()
    venue_id_raw = request.args.get("venue_id", "").strip()
    year_raw = request.args.get("year", "").strip()
    chart_period_raw = request.args.get("chart_period", "").strip().lower()
    start_date_raw = request.args.get("start_date", "").strip()
    end_date_raw = request.args.get("end_date", "").strip()

    current_year = current_date().year
    try:
        selected_year = int(year_raw) if year_raw else current_year
    except (TypeError, ValueError):
        selected_year = current_year
        year_raw = str(current_year)

    selected_chart_period = chart_period_raw if chart_period_raw in {"weekly", "monthly", "yearly", "custom"} else "yearly"
    chart_period = build_report_period_context(
        selected_chart_period,
        selected_year,
        start_date_raw,
        end_date_raw,
    )
    range_start = chart_period["start_date"]
    range_end = chart_period["end_date"]
    previous_start, previous_end = previous_date_range(range_start, range_end)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT event_id, event_name, event_date, event_end_date, price
        FROM events
        ORDER BY event_date DESC, event_name ASC
        """
    )
    report_events = cursor.fetchall()

    cursor.execute(
        """
        SELECT venue_id, venue_name, city, capacity
        FROM venues
        ORDER BY
            CASE
                WHEN LOWER(venue_name) = 'ashton gate stadium' THEN 1
                WHEN LOWER(venue_name) = 'arnolfini' THEN 2
                WHEN LOWER(venue_name) = 'the bristol hippodrome' THEN 3
                WHEN LOWER(venue_name) = 'bristol old vic' THEN 4
                WHEN LOWER(venue_name) = 'bristol central library' THEN 5
                WHEN LOWER(venue_name) = 'royal west of england academy' THEN 6
                WHEN LOWER(venue_name) = 'uwe exhibition centre' THEN 7
                WHEN LOWER(venue_name) IN ('creative space a', 'creative space b', 'community centre a') THEN 9
                ELSE 8
            END,
            CASE
                WHEN LOWER(venue_name) = 'creative space a' THEN 1
                WHEN LOWER(venue_name) = 'creative space b' THEN 2
                WHEN LOWER(venue_name) = 'community centre a' THEN 3
                WHEN LOWER(venue_name) = 'ashton gate stadium'
                  OR LOWER(venue_name) = 'arnolfini'
                  OR LOWER(venue_name) = 'the bristol hippodrome'
                  OR LOWER(venue_name) = 'bristol old vic'
                  OR LOWER(venue_name) = 'bristol central library'
                  OR LOWER(venue_name) = 'royal west of england academy'
                  OR LOWER(venue_name) = 'uwe exhibition centre' THEN 0
                ELSE -venue_id
            END ASC,
            venue_name ASC
        """
    )
    report_venues = cursor.fetchall()

    event_report = None
    venue_report = None
    year_report = None

    if event_id_raw:
        try:
            event_id = int(event_id_raw)
        except (TypeError, ValueError):
            event_id = None
        if event_id:
            cursor.execute(
                """
                SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.price,
                       e.event_cost, e.event_capacity, v.venue_name, c.category_name,
                       COALESCE(bt.booked_tickets, 0) AS booked_tickets,
                       CASE
                            WHEN e.event_capacity IS NULL THEN NULL
                            ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
                       END AS remaining_seats
                FROM events e
                LEFT JOIN venues v ON e.venue_id = v.venue_id
                LEFT JOIN categories c ON e.category_id = c.category_id
                LEFT JOIN (
                    SELECT event_id, SUM(tickets) AS booked_tickets
                    FROM bookings
                    WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                    GROUP BY event_id
                ) bt ON e.event_id = bt.event_id
                WHERE e.event_id = %s
                """,
                (event_id,),
            )
            event_report = cursor.fetchone()
            if event_report:
                cursor.execute(
                    f"""
                    SELECT
                        COUNT(*) AS bookings_count,
                        COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN b.tickets ELSE 0 END), 0) AS tickets_sold,
                        {booking_revenue_total_sql()} AS revenue_total,
                        COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') = 'Cancelled' THEN 1 ELSE 0 END), 0) AS cancelled_bookings
                    FROM bookings b
                    WHERE b.event_id = %s
                    """,
                    (event_id,),
                )
                event_stats = cursor.fetchone() or {}
                event_report.update(event_stats)
                event_report["remaining_seats"] = available_seats(cursor, event_id)
                event_report["profit_total"] = (
                    to_money(event_report.get("revenue_total")) - to_money(event_report.get("event_cost"))
                )
                cursor.execute(
                    """
                    SELECT COUNT(*) AS waitlist_count
                    FROM event_waitlist
                    WHERE event_id=%s
                      AND status IN (%s, %s)
                    """,
                    (event_id, WAITLIST_STATUS_WAITING, WAITLIST_STATUS_OFFERED),
                )
                event_report["waitlist_count"] = cursor.fetchone()["waitlist_count"] or 0

    if venue_id_raw:
        try:
            venue_id = int(venue_id_raw)
        except (TypeError, ValueError):
            venue_id = None
        if venue_id:
            cursor.execute(
                f"""
                SELECT v.venue_id, v.venue_name, v.address, v.city, v.capacity,
                       COALESCE(stats.total_events, 0) AS total_events,
                       COALESCE(stats.upcoming_events, 0) AS upcoming_events,
                       COALESCE(stats.fully_booked_events, 0) AS fully_booked_events,
                       COALESCE(stats.revenue_total, 0) AS revenue_total
                FROM venues v
                LEFT JOIN (
                    SELECT e.venue_id,
                           COUNT(DISTINCT e.event_id) AS total_events,
                           SUM(CASE WHEN e.event_date >= CURDATE() THEN 1 ELSE 0 END) AS upcoming_events,
                           SUM(
                               CASE
                                   WHEN e.event_capacity IS NOT NULL
                                        AND COALESCE(bt.booked_tickets, 0) >= e.event_capacity
                                   THEN 1 ELSE 0
                               END
                           ) AS fully_booked_events,
                           {booking_revenue_total_sql()} AS revenue_total
                    FROM events e
                    LEFT JOIN (
                        SELECT event_id, SUM(tickets) AS booked_tickets
                        FROM bookings
                        WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                        GROUP BY event_id
                    ) bt ON e.event_id = bt.event_id
                    LEFT JOIN bookings b ON b.event_id = e.event_id
                    GROUP BY e.venue_id
                ) stats ON v.venue_id = stats.venue_id
                WHERE v.venue_id = %s
                """,
                (venue_id,),
            )
            venue_report = cursor.fetchone()
            if venue_report:
                cursor.execute(
                    """
                    SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.price,
                           COALESCE(bt.booked_tickets, 0) AS booked_tickets,
                           CASE
                                WHEN e.event_capacity IS NULL THEN NULL
                                ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
                           END AS remaining_seats
                    FROM events e
                    LEFT JOIN (
                        SELECT event_id, SUM(tickets) AS booked_tickets
                        FROM bookings
                        WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                        GROUP BY event_id
                    ) bt ON e.event_id = bt.event_id
                    WHERE e.venue_id = %s
                    ORDER BY e.event_date ASC, e.event_name ASC
                    """,
                    (venue_id,),
                )
                venue_report["events"] = [enrich_booking_event(event) for event in cursor.fetchall()]

    report_booking_rows = fetch_report_booking_rows(cursor, range_start, range_end)
    report_metrics, report_customer_counts = build_report_metrics(report_booking_rows)
    previous_booking_rows = (
        fetch_report_booking_rows(cursor, previous_start, previous_end)
        if previous_start and previous_end
        else []
    )
    previous_metrics, _ = build_report_metrics(previous_booking_rows)
    revenue_trend_labels, revenue_trend_values = build_report_timeseries(
        report_booking_rows,
        range_start,
        range_end,
        chart_period["period_key"],
    )
    top_event_labels, top_event_values = build_report_top_events(report_booking_rows)
    category_labels, category_values = build_report_category_breakdown(report_booking_rows)
    report_customer_rows = build_report_customer_rows(report_booking_rows)
    cancellation_summary = build_report_cancellation_summary(report_booking_rows)
    waitlist_rows = fetch_report_waitlist_rows(cursor, range_start, range_end)
    waitlist_summary = build_report_waitlist_summary(waitlist_rows, report_booking_rows)
    event_mix_counts = fetch_event_mix_counts(cursor, range_start, range_end)

    def change_payload(current_value, previous_value):
        current_num = float(current_value or 0)
        previous_num = float(previous_value or 0)
        delta = current_num - previous_num
        pct = None
        if previous_num:
            pct = round((delta / previous_num) * 100, 1)
        elif current_num:
            pct = 100.0
        return {
            "current": current_num,
            "previous": previous_num,
            "delta": delta,
            "pct": pct,
        }

    report_comparison = {
        "revenue": change_payload(report_metrics["revenue_total"], previous_metrics["revenue_total"]),
        "tickets": change_payload(report_metrics["tickets_sold"], previous_metrics["tickets_sold"]),
        "customers": change_payload(report_metrics["unique_customers"], previous_metrics["unique_customers"]),
        "bookings": change_payload(report_metrics["active_bookings_count"], previous_metrics["active_bookings_count"]),
    }
    report_completion_rate = report_metrics["booking_completion_rate"]
    report_cancellation_rate = report_metrics["cancellation_rate"]

    report_legend = {
        "active": "Active booked",
        "cancelled": "Cancelled only",
        "none": "No booking",
    }

    cursor.execute(
        f"""
        SELECT
            COUNT(DISTINCT CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN e.event_id END) AS successful_events_count,
            COUNT(DISTINCT e.event_id) AS total_events_in_year,
            {booking_revenue_total_sql()} AS revenue_total,
            COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN b.tickets ELSE 0 END), 0) AS tickets_sold
        FROM events e
        LEFT JOIN bookings b ON b.event_id = e.event_id
        WHERE YEAR(e.event_date) = %s
        """,
        (selected_year,),
    )
    year_report = cursor.fetchone() or {}
    year_report["selected_year"] = selected_year
    cursor.execute(
        """
        SELECT COALESCE(SUM(COALESCE(event_cost, 0)), 0) AS total
        FROM events
        WHERE YEAR(event_date) = %s
        """,
        (selected_year,),
    )
    year_report["cost_total"] = cursor.fetchone()["total"] or 0
    year_report["profit_total"] = to_money(year_report.get("revenue_total")) - to_money(
        year_report.get("cost_total")
    )
    year_report["fully_booked_events_count"] = 0
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM events e
        LEFT JOIN (
            SELECT event_id, SUM(tickets) AS booked_tickets
            FROM bookings
            WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
            GROUP BY event_id
        ) bt ON e.event_id = bt.event_id
        WHERE YEAR(e.event_date) = %s
          AND e.event_capacity IS NOT NULL
          AND COALESCE(bt.booked_tickets, 0) >= e.event_capacity
        """,
        (selected_year,),
    )
    year_report["fully_booked_events_count"] = cursor.fetchone()["count"] or 0
    year_report["event_mix_period_key"] = selected_chart_period
    year_report["event_mix_title"] = chart_period["title"]
    year_report["event_mix_note"] = chart_period["note"]
    year_report["event_mix_labels"] = [
        "Active booked events",
        "Cancelled-only events",
        "No booking events",
    ]
    year_report["event_mix_values"] = [
        event_mix_counts.get("active_booked_events_count") or 0,
        event_mix_counts.get("cancelled_only_events_count") or 0,
        event_mix_counts.get("no_booking_events_count") or 0,
    ]
    year_report["event_mix_colors"] = ["#1a5276", "#e74c3c", "#f39c12"]

    cursor.close()
    conn.close()

    return render_template(
        "admin/reports.html",
        report_events=report_events,
        report_venues=report_venues,
        event_report=event_report,
        venue_report=venue_report,
        year_report=year_report,
        report_period=chart_period,
        report_metrics=report_metrics,
        report_comparison=report_comparison,
        report_revenue_trend_labels=revenue_trend_labels,
        report_revenue_trend_values=revenue_trend_values,
        report_top_event_labels=top_event_labels,
        report_top_event_values=top_event_values,
        report_category_labels=category_labels,
        report_category_values=category_values,
        report_customers=report_customer_rows,
        cancellation_summary=cancellation_summary,
        waitlist_summary=waitlist_summary,
        report_completion_rate=report_completion_rate,
        report_cancellation_rate=report_cancellation_rate,
        report_legend=report_legend,
        report_booking_rows=report_booking_rows,
        report_waitlist_rows=waitlist_rows,
        selected_event_id=event_id_raw,
        selected_venue_id=venue_id_raw,
        selected_year=selected_year,
        selected_chart_period=selected_chart_period,
        selected_start_date=range_start.isoformat() if range_start else "",
        selected_end_date=range_end.isoformat() if range_end else "",
    )


def build_admin_report_data(event_id_raw, venue_id_raw, year_raw, chart_period_raw, start_date_raw, end_date_raw):
    current_year = current_date().year
    try:
        selected_year = int(year_raw) if year_raw else current_year
    except (TypeError, ValueError):
        selected_year = current_year

    selected_chart_period = chart_period_raw if chart_period_raw in {"weekly", "monthly", "yearly", "custom"} else "yearly"
    report_period = build_report_period_context(
        selected_chart_period,
        selected_year,
        start_date_raw,
        end_date_raw,
    )
    range_start = report_period["start_date"]
    range_end = report_period["end_date"]
    previous_start, previous_end = previous_date_range(range_start, range_end)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT event_id, event_name, event_date, event_end_date, price
            FROM events
            ORDER BY event_date DESC, event_name ASC
            """
        )
        report_events = cursor.fetchall()

        cursor.execute(
            """
            SELECT venue_id, venue_name, city, capacity
            FROM venues
            ORDER BY
                CASE
                    WHEN LOWER(venue_name) = 'ashton gate stadium' THEN 1
                    WHEN LOWER(venue_name) = 'arnolfini' THEN 2
                    WHEN LOWER(venue_name) = 'the bristol hippodrome' THEN 3
                    WHEN LOWER(venue_name) = 'bristol old vic' THEN 4
                    WHEN LOWER(venue_name) = 'bristol central library' THEN 5
                    WHEN LOWER(venue_name) = 'royal west of england academy' THEN 6
                    WHEN LOWER(venue_name) = 'uwe exhibition centre' THEN 7
                    WHEN LOWER(venue_name) IN ('creative space a', 'creative space b', 'community centre a') THEN 9
                    ELSE 8
                END,
                CASE
                    WHEN LOWER(venue_name) = 'creative space a' THEN 1
                    WHEN LOWER(venue_name) = 'creative space b' THEN 2
                    WHEN LOWER(venue_name) = 'community centre a' THEN 3
                    WHEN LOWER(venue_name) = 'ashton gate stadium'
                      OR LOWER(venue_name) = 'arnolfini'
                      OR LOWER(venue_name) = 'the bristol hippodrome'
                      OR LOWER(venue_name) = 'bristol old vic'
                      OR LOWER(venue_name) = 'bristol central library'
                      OR LOWER(venue_name) = 'royal west of england academy'
                      OR LOWER(venue_name) = 'uwe exhibition centre' THEN 0
                    ELSE -venue_id
                END ASC,
                venue_name ASC
            """
        )
        report_venues = cursor.fetchall()

        event_report = None
        venue_report = None

        if event_id_raw:
            try:
                event_id = int(event_id_raw)
            except (TypeError, ValueError):
                event_id = None
            if event_id:
                cursor.execute(
                    """
                    SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.price,
                           e.event_cost, e.event_capacity, v.venue_name, c.category_name,
                           COALESCE(bt.booked_tickets, 0) AS booked_tickets,
                           CASE
                                WHEN e.event_capacity IS NULL THEN NULL
                                ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
                           END AS remaining_seats
                    FROM events e
                    LEFT JOIN venues v ON e.venue_id = v.venue_id
                    LEFT JOIN categories c ON e.category_id = c.category_id
                    LEFT JOIN (
                        SELECT event_id, SUM(tickets) AS booked_tickets
                        FROM bookings
                        WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                        GROUP BY event_id
                    ) bt ON e.event_id = bt.event_id
                    WHERE e.event_id = %s
                    """,
                    (event_id,),
                )
                event_report = cursor.fetchone()
                if event_report:
                    cursor.execute(
                        f"""
                        SELECT
                            COUNT(*) AS bookings_count,
                            COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN b.tickets ELSE 0 END), 0) AS tickets_sold,
                            {booking_revenue_total_sql()} AS revenue_total,
                            COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') = 'Cancelled' THEN 1 ELSE 0 END), 0) AS cancelled_bookings
                        FROM bookings b
                        WHERE b.event_id = %s
                        """,
                        (event_id,),
                    )
                    event_stats = cursor.fetchone() or {}
                    event_report.update(event_stats)
                    event_report["remaining_seats"] = available_seats(cursor, event_id)
                    event_report["profit_total"] = (
                        to_money(event_report.get("revenue_total")) - to_money(event_report.get("event_cost"))
                    )
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS waitlist_count
                        FROM event_waitlist
                        WHERE event_id=%s
                          AND status IN (%s, %s)
                        """,
                        (event_id, WAITLIST_STATUS_WAITING, WAITLIST_STATUS_OFFERED),
                    )
                    event_report["waitlist_count"] = cursor.fetchone()["waitlist_count"] or 0

        if venue_id_raw:
            try:
                venue_id = int(venue_id_raw)
            except (TypeError, ValueError):
                venue_id = None
            if venue_id:
                cursor.execute(
                    f"""
                    SELECT v.venue_id, v.venue_name, v.address, v.city, v.capacity,
                           COALESCE(stats.total_events, 0) AS total_events,
                           COALESCE(stats.upcoming_events, 0) AS upcoming_events,
                           COALESCE(stats.fully_booked_events, 0) AS fully_booked_events,
                           COALESCE(stats.revenue_total, 0) AS revenue_total
                    FROM venues v
                    LEFT JOIN (
                        SELECT e.venue_id,
                               COUNT(DISTINCT e.event_id) AS total_events,
                               SUM(CASE WHEN e.event_date >= CURDATE() THEN 1 ELSE 0 END) AS upcoming_events,
                               SUM(
                                   CASE
                                       WHEN e.event_capacity IS NOT NULL
                                            AND COALESCE(bt.booked_tickets, 0) >= e.event_capacity
                                       THEN 1 ELSE 0
                                   END
                               ) AS fully_booked_events,
                               {booking_revenue_total_sql()} AS revenue_total
                        FROM events e
                        LEFT JOIN (
                            SELECT event_id, SUM(tickets) AS booked_tickets
                            FROM bookings
                            WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                            GROUP BY event_id
                        ) bt ON e.event_id = bt.event_id
                        LEFT JOIN bookings b ON b.event_id = e.event_id
                        GROUP BY e.venue_id
                    ) stats ON v.venue_id = stats.venue_id
                    WHERE v.venue_id = %s
                    """,
                    (venue_id,),
                )
                venue_report = cursor.fetchone()
                if venue_report:
                    cursor.execute(
                        """
                        SELECT e.event_id, e.event_name, e.event_date, e.event_end_date, e.price,
                               COALESCE(bt.booked_tickets, 0) AS booked_tickets,
                               CASE
                                    WHEN e.event_capacity IS NULL THEN NULL
                                    ELSE GREATEST(e.event_capacity - COALESCE(bt.booked_tickets, 0), 0)
                               END AS remaining_seats
                        FROM events e
                        LEFT JOIN (
                            SELECT event_id, SUM(tickets) AS booked_tickets
                            FROM bookings
                            WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                            GROUP BY event_id
                        ) bt ON e.event_id = bt.event_id
                        WHERE e.venue_id = %s
                        ORDER BY e.event_date ASC, e.event_name ASC
                        """,
                        (venue_id,),
                    )
                    venue_report["events"] = [enrich_booking_event(event) for event in cursor.fetchall()]

        report_booking_rows = fetch_report_booking_rows(cursor, range_start, range_end)
        report_metrics, _ = build_report_metrics(report_booking_rows)
        previous_booking_rows = (
            fetch_report_booking_rows(cursor, previous_start, previous_end)
            if previous_start and previous_end
            else []
        )
        previous_metrics, _ = build_report_metrics(previous_booking_rows)
        revenue_trend_labels, revenue_trend_values = build_report_timeseries(
            report_booking_rows,
            range_start,
            range_end,
            report_period["period_key"],
        )
        top_event_labels, top_event_values = build_report_top_events(report_booking_rows)
        category_labels, category_values = build_report_category_breakdown(report_booking_rows)
        report_customer_rows = build_report_customer_rows(report_booking_rows)
        cancellation_summary = build_report_cancellation_summary(report_booking_rows)
        waitlist_rows = fetch_report_waitlist_rows(cursor, range_start, range_end)
        waitlist_summary = build_report_waitlist_summary(waitlist_rows, report_booking_rows)
        event_mix_counts = fetch_event_mix_counts(cursor, range_start, range_end)

        def change_payload(current_value, previous_value):
            current_num = float(current_value or 0)
            previous_num = float(previous_value or 0)
            delta = current_num - previous_num
            pct = None
            if previous_num:
                pct = round((delta / previous_num) * 100, 1)
            elif current_num:
                pct = 100.0
            return {
                "current": current_num,
                "previous": previous_num,
                "delta": delta,
                "pct": pct,
            }

        report_comparison = {
            "revenue": change_payload(report_metrics["revenue_total"], previous_metrics["revenue_total"]),
            "tickets": change_payload(report_metrics["tickets_sold"], previous_metrics["tickets_sold"]),
            "customers": change_payload(report_metrics["unique_customers"], previous_metrics["unique_customers"]),
            "bookings": change_payload(report_metrics["active_bookings_count"], previous_metrics["active_bookings_count"]),
        }

        cursor.execute(
            f"""
            SELECT
                COUNT(DISTINCT CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN e.event_id END) AS successful_events_count,
                COUNT(DISTINCT e.event_id) AS total_events_in_year,
                {booking_revenue_total_sql()} AS revenue_total,
                COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN b.tickets ELSE 0 END), 0) AS tickets_sold
            FROM events e
            LEFT JOIN bookings b ON b.event_id = e.event_id
            WHERE YEAR(e.event_date) = %s
            """,
            (selected_year,),
        )
        year_report = cursor.fetchone() or {}
        year_report["selected_year"] = selected_year
        cursor.execute(
            """
            SELECT COALESCE(SUM(COALESCE(event_cost, 0)), 0) AS total
            FROM events
            WHERE YEAR(event_date) = %s
            """,
            (selected_year,),
        )
        year_report["cost_total"] = cursor.fetchone()["total"] or 0
        year_report["profit_total"] = to_money(year_report.get("revenue_total")) - to_money(
            year_report.get("cost_total")
        )
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM events e
            LEFT JOIN (
                SELECT event_id, SUM(tickets) AS booked_tickets
                FROM bookings
                WHERE COALESCE(status, 'Confirmed') <> 'Cancelled'
                GROUP BY event_id
            ) bt ON e.event_id = bt.event_id
            WHERE YEAR(e.event_date) = %s
              AND e.event_capacity IS NOT NULL
              AND COALESCE(bt.booked_tickets, 0) >= e.event_capacity
            """,
            (selected_year,),
        )
        year_report["fully_booked_events_count"] = cursor.fetchone()["count"] or 0
        year_report["event_mix_period_key"] = report_period["period_key"]
        year_report["event_mix_title"] = report_period["title"]
        year_report["event_mix_note"] = report_period["note"]
        year_report["event_mix_labels"] = [
            "Active booked events",
            "Cancelled-only events",
            "No booking events",
        ]
        year_report["event_mix_values"] = [
            event_mix_counts.get("active_booked_events_count") or 0,
            event_mix_counts.get("cancelled_only_events_count") or 0,
            event_mix_counts.get("no_booking_events_count") or 0,
        ]
        year_report["event_mix_colors"] = ["#1a5276", "#e74c3c", "#f39c12"]

        return {
            "report_events": report_events,
            "report_venues": report_venues,
            "event_report": event_report,
            "venue_report": venue_report,
            "year_report": year_report,
            "report_period": report_period,
            "report_metrics": report_metrics,
            "report_comparison": report_comparison,
            "report_revenue_trend_labels": revenue_trend_labels,
            "report_revenue_trend_values": revenue_trend_values,
            "report_top_event_labels": top_event_labels,
            "report_top_event_values": top_event_values,
            "report_category_labels": category_labels,
            "report_category_values": category_values,
            "report_customers": report_customer_rows,
            "cancellation_summary": cancellation_summary,
            "waitlist_summary": waitlist_summary,
            "report_completion_rate": report_metrics["booking_completion_rate"],
            "report_cancellation_rate": report_metrics["cancellation_rate"],
            "selected_event_id": event_id_raw,
            "selected_venue_id": venue_id_raw,
            "selected_year": selected_year,
            "selected_chart_period": report_period["period_key"],
            "selected_start_date": range_start.isoformat() if range_start else "",
            "selected_end_date": range_end.isoformat() if range_end else "",
        }
    finally:
        cursor.close()
        conn.close()


@admin_required
def admin_reports_export_csv():
    event_id_raw = request.args.get("event_id", "").strip()
    venue_id_raw = request.args.get("venue_id", "").strip()
    year_raw = request.args.get("year", "").strip()
    chart_period_raw = request.args.get("chart_period", "").strip().lower()
    start_date_raw = request.args.get("start_date", "").strip()
    end_date_raw = request.args.get("end_date", "").strip()

    current_year = current_date().year
    try:
        selected_year = int(year_raw) if year_raw else current_year
    except (TypeError, ValueError):
        selected_year = current_year

    selected_chart_period = chart_period_raw if chart_period_raw in {"weekly", "monthly", "yearly", "custom"} else "yearly"
    report_period = build_report_period_context(
        selected_chart_period,
        selected_year,
        start_date_raw,
        end_date_raw,
    )
    range_start = report_period["start_date"]
    range_end = report_period["end_date"]
    previous_start, previous_end = previous_date_range(range_start, range_end)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        report_booking_rows = fetch_report_booking_rows(cursor, range_start, range_end)
        report_metrics, _ = build_report_metrics(report_booking_rows)
        previous_booking_rows = (
            fetch_report_booking_rows(cursor, previous_start, previous_end)
            if previous_start and previous_end
            else []
        )
        previous_metrics, _ = build_report_metrics(previous_booking_rows)
        revenue_trend_labels, revenue_trend_values = build_report_timeseries(
            report_booking_rows,
            range_start,
            range_end,
            report_period["period_key"],
        )
        top_event_labels, top_event_values = build_report_top_events(report_booking_rows)
        category_labels, category_values = build_report_category_breakdown(report_booking_rows)
        report_customers = build_report_customer_rows(report_booking_rows)
        cancellation_summary = build_report_cancellation_summary(report_booking_rows)
        waitlist_rows = fetch_report_waitlist_rows(cursor, range_start, range_end)
        waitlist_summary = build_report_waitlist_summary(waitlist_rows, report_booking_rows)

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["Bristol Community Events Admin Report Export"])
        writer.writerow(["Period", report_period["title"]])
        writer.writerow(["Range start", range_start.isoformat()])
        writer.writerow(["Range end", range_end.isoformat()])
        writer.writerow([])
        writer.writerow(["Summary"])
        writer.writerow(["Metric", "Current", "Previous", "Delta", "Delta %"])
        summary_rows = [
            ("Revenue", report_metrics["revenue_total"], previous_metrics["revenue_total"]),
            ("Tickets sold", report_metrics["tickets_sold"], previous_metrics["tickets_sold"]),
            ("Active bookings", report_metrics["active_bookings_count"], previous_metrics["active_bookings_count"]),
            ("Unique customers", report_metrics["unique_customers"], previous_metrics["unique_customers"]),
            ("Repeat bookers", report_metrics["repeat_bookers"], previous_metrics["repeat_bookers"]),
            ("Completion rate %", report_metrics["booking_completion_rate"], previous_metrics["booking_completion_rate"]),
            ("Cancellation rate %", report_metrics["cancellation_rate"], previous_metrics["cancellation_rate"]),
        ]
        for label, current_value, previous_value in summary_rows:
            delta = float(current_value or 0) - float(previous_value or 0)
            pct = None
            prev_num = float(previous_value or 0)
            if prev_num:
                pct = round((delta / prev_num) * 100, 1)
            elif float(current_value or 0):
                pct = 100.0
            writer.writerow([label, current_value, previous_value, delta, pct if pct is not None else ""])

        writer.writerow([])
        writer.writerow(["Revenue over time"])
        writer.writerow(["Bucket", "Revenue"])
        for label, value in zip(revenue_trend_labels, revenue_trend_values):
            writer.writerow([label, value])

        writer.writerow([])
        writer.writerow(["Top 5 events by revenue"])
        writer.writerow(["Event", "Revenue"])
        for label, value in zip(top_event_labels, top_event_values):
            writer.writerow([label, value])

        writer.writerow([])
        writer.writerow(["Category breakdown"])
        writer.writerow(["Category", "Tickets sold"])
        for label, value in zip(category_labels, category_values):
            writer.writerow([label, value])

        writer.writerow([])
        writer.writerow(["Customer report"])
        writer.writerow(["Customer", "Bookings", "Spend"])
        for row in report_customers:
            writer.writerow([row["name"], row["bookings"], row["spend"]])

        writer.writerow([])
        writer.writerow(["Cancellation report"])
        writer.writerow(["Cancelled bookings", cancellation_summary["cancelled_count"]])
        writer.writerow(["Cancellation rate %", cancellation_summary["cancellation_rate"]])
        writer.writerow(["Revenue lost", cancellation_summary["lost_revenue"]])
        writer.writerow(["Cancellation charges collected", cancellation_summary["charge_collected"]])

        writer.writerow([])
        writer.writerow(["Waitlist report"])
        writer.writerow(["Waitlist requests", waitlist_summary["total_requests"]])
        writer.writerow(["Waitlist tickets", waitlist_summary["total_tickets"]])
        writer.writerow(["Event", "Requests", "Tickets"])
        for row in waitlist_summary["top_events"]:
            writer.writerow([row["label"], row["requests"], row["tickets"]])

        response = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
        filename = f"admin_reports_{range_start.isoformat()}_{range_end.isoformat()}.csv"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        return response
    finally:
        cursor.close()
        conn.close()


@admin_required
def admin_reports_export_pdf():
    event_id_raw = request.args.get("event_id", "").strip()
    venue_id_raw = request.args.get("venue_id", "").strip()
    year_raw = request.args.get("year", "").strip()
    chart_period_raw = request.args.get("chart_period", "").strip().lower()
    start_date_raw = request.args.get("start_date", "").strip()
    end_date_raw = request.args.get("end_date", "").strip()

    report_data = build_admin_report_data(
        event_id_raw,
        venue_id_raw,
        year_raw,
        chart_period_raw,
        start_date_raw,
        end_date_raw,
    )
    pdf_bytes = build_admin_reports_pdf(report_data)
    range_start = report_data.get("selected_start_date") or current_date().isoformat()
    range_end = report_data.get("selected_end_date") or current_date().isoformat()
    filename = f"admin_reports_{range_start}_{range_end}.pdf"
    response = Response(pdf_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@admin_required
def admin_waitlist():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    waitlist_entries = fetch_active_waitlist_entries(
        cursor,
        limit=100,
        order_by="CASE WHEN w.status = 'Offered' THEN 0 ELSE 1 END, w.created_at ASC, w.waitlist_id ASC",
    )

    queue_summary = {
        "active_count": len(waitlist_entries),
        "offered_count": sum(1 for entry in waitlist_entries if entry.get("status") == WAITLIST_STATUS_OFFERED),
        "waiting_count": sum(1 for entry in waitlist_entries if entry.get("status") == WAITLIST_STATUS_WAITING),
    }

    cursor.close()
    conn.close()

    return render_template(
        "admin/waitlist.html",
        waitlist_entries=waitlist_entries,
        queue_summary=queue_summary,
    )


@admin_required
def admin_subscribers():
    return render_template("admin/subscribers.html")


@admin_required
def admin_venues():
    q = request.args.get("q", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venues = fetch_venue_overview(cursor, q=q)
    cursor.close()
    conn.close()

    return render_template(
        "admin/venues.html",
        venues=venues,
        q=q,
        total_venues=len(venues),
    )


@admin_required
def add_venue():
    venue = {}

    if request.method == "POST":
        venue_name = request.form.get("venue_name", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        suitable_for = request.form.get("suitable_for", "").strip()
        image_url = request.form.get("image_url", "").strip()
        capacity_raw = request.form.get("capacity", "").strip()

        try:
            capacity = int(capacity_raw)
        except (TypeError, ValueError):
            capacity = None

        venue = {
            "venue_name": venue_name,
            "address": address,
            "city": city,
            "suitable_for": suitable_for,
            "image_url": image_url,
            "capacity": capacity,
        }

        if not venue_name:
            flash("Please enter a venue name.", "error")
        elif capacity is None or capacity < 1:
            flash("Please enter a valid venue capacity.", "error")
        else:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            try:
                if venue_name_exists(cursor, venue_name):
                    flash("A venue with that name already exists.", "error")
                else:
                    cursor.execute(
                        """
                        INSERT INTO venues (venue_name, address, city, suitable_for, image_url, capacity)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            venue_name[:150],
                            address[:255] or None,
                            city[:100] or None,
                            suitable_for[:255] or None,
                            image_url[:500] or None,
                            capacity,
                        ),
                    )
                    conn.commit()
                    flash("Venue added successfully.", "success")
                    return redirect(url_for("admin_venues"))
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")
            finally:
                cursor.close()
                conn.close()

    return render_template(
        "venue_form.html",
        page_title="Add Venue",
        submit_label="Add Venue",
        action_url=url_for("add_venue"),
        venue=venue,
    )


@admin_required
def update_venue(venue_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venue_rows = fetch_venue_overview(cursor, venue_id=venue_id, limit=1)

    if not venue_rows:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    venue = venue_rows[0]

    try:
        if request.method == "POST":
            venue_name = request.form.get("venue_name", "").strip()
            address = request.form.get("address", "").strip()
            city = request.form.get("city", "").strip()
            suitable_for = request.form.get("suitable_for", "").strip()
            image_url = request.form.get("image_url", "").strip()
            capacity_raw = request.form.get("capacity", "").strip()

            try:
                capacity = int(capacity_raw)
            except (TypeError, ValueError):
                capacity = None

            if not venue_name:
                flash("Please enter a venue name.", "error")
            elif capacity is None or capacity < 1:
                flash("Please enter a valid venue capacity.", "error")
            else:
                try:
                    if venue_name_exists(cursor, venue_name, exclude_venue_id=venue_id):
                        flash("A venue with that name already exists.", "error")
                    else:
                        cursor.execute(
                            """
                            UPDATE venues
                            SET venue_name=%s, address=%s, city=%s, suitable_for=%s, image_url=%s, capacity=%s
                            WHERE venue_id=%s
                            """,
                            (
                                venue_name[:150],
                                address[:255] or None,
                                city[:100] or None,
                                suitable_for[:255] or None,
                                image_url[:500] or None,
                                capacity,
                                venue_id,
                            ),
                        )
                        conn.commit()
                        flash("Venue updated successfully.", "success")
                        return redirect(url_for("admin_venues"))
                except mysql.connector.Error as err:
                    conn.rollback()
                    flash(f"Database error: {err}", "error")

            venue.update(
                {
                    "venue_name": venue_name,
                    "address": address,
                    "city": city,
                    "suitable_for": suitable_for,
                    "image_url": image_url,
                    "capacity": capacity,
                }
            )
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "venue_form.html",
        page_title="Edit Venue",
        submit_label="Update Venue",
        action_url=url_for("update_venue", venue_id=venue_id),
        venue=venue,
    )


@admin_required
def delete_venue(venue_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    venue_rows = fetch_venue_overview(cursor, venue_id=venue_id, limit=1)

    if not venue_rows:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    venue = venue_rows[0]

    try:
        if request.method == "POST":
            try:
                cursor.execute("DELETE FROM venues WHERE venue_id=%s", (venue_id,))
                conn.commit()
                flash("Venue deleted successfully.", "success")
                return redirect(url_for("admin_venues"))
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return render_template("venue_delete.html", venue=venue)


@admin_required
def admin_bookings():
    q = request.args.get("q", "").strip()
    event_id_raw = request.args.get("event_id", "").strip()
    status_raw = request.args.get("status", "").strip()
    date_from_raw = request.args.get("date_from", "").strip()
    date_to_raw = request.args.get("date_to", "").strip()

    date_from = parse_event_date(date_from_raw)
    date_to = parse_event_date(date_to_raw)
    booked_at_sql = booking_booked_at_sql()

    filters = []
    params = []

    if q:
        like = f"%{q}%"
        filters.append("(u.full_name LIKE %s OR u.email LIKE %s OR e.event_name LIKE %s)")
        params.extend([like, like, like])

    if event_id_raw:
        try:
            event_id = int(event_id_raw)
        except (TypeError, ValueError):
            event_id = None
        if event_id:
            filters.append("e.event_id = %s")
            params.append(event_id)

    if status_raw:
        if status_raw in BOOKING_STATUSES:
            filters.append("COALESCE(b.status, 'Confirmed') = %s")
            params.append(status_raw)
        else:
            flash("Invalid status filter.", "error")

    if date_from is None and date_from_raw:
        flash("Invalid start date filter.", "error")
    elif date_from is not None:
        filters.append(f"DATE({booked_at_sql}) >= %s")
        params.append(date_from)

    if date_to is None and date_to_raw:
        flash("Invalid end date filter.", "error")
    elif date_to is not None:
        filters.append(f"DATE({booked_at_sql}) <= %s")
        params.append(date_to)

    where_clause = " AND ".join(filters) if filters else "1=1"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        f"""
        SELECT b.booking_id, b.booking_date, b.created_at, {booked_at_sql} AS booked_at,
               b.tickets, b.booking_days, b.is_student,
               b.subtotal_amount, b.student_discount_amount, b.advance_discount_amount,
               b.discount_applied, b.cancellation_charge, b.refund_amount,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_id, e.event_name, e.event_date, e.event_end_date,
               p.amount, p.payment_method, COALESCE(p.payment_status, 'Pending') AS payment_status
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        WHERE {where_clause}
        ORDER BY booked_at DESC, b.booking_id DESC
        """,
        tuple(params),
    )
    bookings = cursor.fetchall()

    cursor.execute("SELECT event_id, event_name FROM events ORDER BY event_name")
    events_rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "admin/bookings.html",
        bookings=bookings,
        events=events_rows,
        statuses=BOOKING_STATUSES,
        q=q,
        event_id=event_id_raw,
        status=status_raw,
        date_from=date_from_raw,
        date_to=date_to_raw,
    )


@admin_required
def admin_receipts():
    q = request.args.get("q", "").strip()
    page = parse_positive_int(request.args.get("page"), 1)
    per_page = 10

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    total_receipts = count_booking_receipts(cursor, q=q)
    total_pages = max(1, (total_receipts + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    receipts = fetch_booking_receipts(
        cursor,
        q=q,
        limit=per_page,
        offset=offset,
    )
    for receipt in receipts:
        receipt["receipt_reference"] = booking_receipt_reference(receipt["booking_id"])

    pagination_base_args = {}
    if q:
        pagination_base_args["q"] = q

    pagination_links = []
    for item in build_pagination_pages(page, total_pages):
        if item is None:
            pagination_links.append({"type": "ellipsis"})
        else:
            page_args = dict(pagination_base_args)
            page_args["page"] = item
            pagination_links.append(
                {
                    "type": "page",
                    "page": item,
                    "url": url_for("admin_receipts", **page_args),
                }
            )

    cursor.close()
    conn.close()

    return render_template(
        "admin/receipts.html",
        receipts=receipts,
        q=q,
        page=page,
        total_pages=total_pages,
        total_receipts=total_receipts,
        start_item=((page - 1) * per_page + 1) if total_receipts else 0,
        end_item=min(page * per_page, total_receipts) if total_receipts else 0,
        pagination_links=pagination_links,
        pagination_prev_url=url_for("admin_receipts", page=page - 1, **pagination_base_args) if page > 1 else None,
        pagination_next_url=url_for("admin_receipts", page=page + 1, **pagination_base_args) if page < total_pages else None,
    )


@login_required
def user_edit_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    booking = fetch_booking_details(cursor, booking_id)

    if not booking:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if not can_access_booking(booking):
        cursor.close()
        conn.close()
        abort(403)

    if (booking.get("status") or "").strip() == "Cancelled":
        cursor.close()
        conn.close()
        flash("Cancelled bookings cannot be edited.", "error")
        return redirect(url_for("my_bookings"))

    event_date = booking.get("event_date")
    if not event_date:
        cursor.close()
        conn.close()
        flash("This booking does not have a valid event date, so it cannot be edited.", "error")
        return redirect(url_for("my_bookings"))

    if event_date < current_date():
        cursor.close()
        conn.close()
        flash(
            "This booking cannot be edited because the event date "
            f"({event_date.strftime('%B %d, %Y')}) has already passed. Today is {current_date().strftime('%B %d, %Y')}.",
            "error",
        )
        return redirect(url_for("my_bookings"))

    max_tickets = booking_edit_max_tickets(cursor, booking, booking_id)
    booking["can_edit"] = True
    booking["max_tickets"] = max_tickets
    booking["contact_phone"] = booking_contact_phone_value(booking)
    booking["remaining_for_update"] = available_seats(cursor, booking["event_id"], exclude_booking_id=booking_id)

    if request.method == "POST":
        tickets = parse_ticket_count(request.form.get("tickets"))
        is_student = "is_student" in request.form
        contact_phone = (request.form.get("contact_phone") or "").strip() or None
        now = current_datetime()

        if tickets is None:
            cursor.close()
            conn.close()
            flash("Tickets must be between 1 and 10.", "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))

        if contact_phone and len(contact_phone) > 50:
            cursor.close()
            conn.close()
            flash("Contact phone must be 50 characters or fewer.", "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))

        if tickets > max_tickets:
            cursor.close()
            conn.close()
            flash(f"Only {max_tickets} ticket(s) are available for this update.", "error")
            return redirect(url_for("edit_booking", booking_id=booking_id))

        pricing = calculate_booking_breakdown(
            booking.get("price"),
            tickets,
            is_student,
            booking_days=booking.get("booking_days") or 1,
            event_duration=event_duration_days(booking),
            event_date=booking.get("event_date"),
            booked_at=now,
        )

        try:
            cursor.execute(
                """
                UPDATE bookings
                SET tickets=%s,
                    is_student=%s,
                    contact_phone=%s,
                    subtotal_amount=%s,
                    student_discount_amount=%s,
                    advance_discount_amount=%s,
                    discount_applied=%s
                WHERE booking_id=%s
                """,
                (
                    tickets,
                    is_student,
                    contact_phone,
                    pricing["base_subtotal"],
                    pricing["student_discount"],
                    pricing["advance_discount"],
                    pricing["discount_total"],
                    booking_id,
                ),
            )

            if booking.get("payment_id"):
                cursor.execute(
                    """
                    UPDATE payments
                    SET amount=%s,
                        payment_status=%s,
                        payment_date=%s
                    WHERE booking_id=%s
                    """,
                    (
                        pricing["total"],
                        payment_status_for_booking("Confirmed"),
                        now,
                        booking_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO payments (booking_id, amount, payment_method, payment_status, payment_date)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        booking_id,
                        pricing["total"],
                        booking.get("payment_method") or "card",
                        payment_status_for_booking("Confirmed"),
                        now,
                    ),
                )

            conn.commit()
            flash("Your booking has been updated successfully.", "success")
            cursor.close()
            conn.close()
            return redirect(url_for("booking_receipt", booking_id=booking_id))
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")

    cursor.close()
    conn.close()
    return render_template(
        "edit_booking.html",
        booking=booking,
        max_tickets=max_tickets,
    )


@admin_required
def admin_edit_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        tickets = parse_ticket_count(request.form.get("tickets"))
        booking_days = None
        is_student = "is_student" in request.form
        status = request.form.get("status", "Confirmed").strip()

        if tickets is None:
            cursor.close()
            conn.close()
            flash("Tickets must be between 1 and 10.", "error")
            return redirect(url_for("admin_edit_booking", booking_id=booking_id))

        if status not in BOOKING_STATUSES:
            cursor.close()
            conn.close()
            flash("Please choose a valid booking status.", "error")
            return redirect(url_for("admin_edit_booking", booking_id=booking_id))

        booking = fetch_booking_details(cursor, booking_id)
        if not booking:
            cursor.close()
            conn.close()
            return render_template("404.html"), 404

        booking_days = parse_booking_days(
            request.form.get("booking_days"),
            event_duration_days(booking),
        )
        if booking_days is None:
            booking_days = parse_positive_int(booking.get("booking_days"), 1)

        if status != "Cancelled":
            remaining = available_seats(cursor, booking["event_id"], exclude_booking_id=booking_id)
            if remaining is not None and tickets > remaining:
                cursor.close()
                conn.close()
                flash(f"Only {remaining} seat(s) are available for this event.", "error")
                return redirect(url_for("admin_edit_booking", booking_id=booking_id))

        pricing = calculate_booking_breakdown(
            booking.get("price"),
            tickets,
            is_student,
            booking_days=booking_days,
            event_duration=event_duration_days(booking),
            event_date=booking.get("event_date"),
            booked_at=current_datetime(),
        )
        now = current_datetime()
        cancellation_charge = Decimal("0.00")
        refund_amount = Decimal("0.00")
        payment_status = payment_status_for_booking(status)

        if status == "Cancelled":
            cancellation_preview = calculate_cancellation_charge(
                pricing["total"],
                booking.get("event_date"),
                now,
            )
            cancellation_charge = cancellation_preview["cancellation_charge"]
            refund_amount = cancellation_preview["refund_amount"]
            payment_status = payment_status_for_cancellation(refund_amount)

        try:
            cursor.execute(
                """
                UPDATE bookings
                SET tickets=%s,
                    booking_days=%s,
                    is_student=%s,
                    subtotal_amount=%s,
                    student_discount_amount=%s,
                    advance_discount_amount=%s,
                    discount_applied=%s,
                    cancellation_charge=%s,
                    refund_amount=%s,
                    status=%s
                WHERE booking_id=%s
                """,
                (
                    tickets,
                    booking_days,
                    is_student,
                    pricing["base_subtotal"],
                    pricing["student_discount"],
                    pricing["advance_discount"],
                    pricing["discount_total"],
                    cancellation_charge,
                    refund_amount,
                    status,
                    booking_id,
                ),
            )

            if booking.get("payment_id"):
                cursor.execute(
                    """
                    UPDATE payments
                    SET amount=%s, payment_status=%s, payment_date=%s
                    WHERE booking_id=%s
                    """,
                    (pricing["total"], payment_status, now, booking_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO payments (booking_id, amount, payment_method, payment_status, payment_date)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        booking_id,
                        pricing["total"],
                        "card",
                        payment_status,
                        now,
                    ),
                )

            if status == "Cancelled":
                promote_waitlist_entries(cursor, conn, booking["event_id"])

            conn.commit()
            flash("Booking updated successfully.", "success")
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for("admin_bookings"))

    booking = fetch_booking_details(cursor, booking_id)

    if not booking:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    max_tickets = 10
    seats_available = available_seats(cursor, booking["event_id"], exclude_booking_id=booking_id)
    if seats_available is not None and seats_available > 0:
        max_tickets = min(10, seats_available)

    booking["available_for_update"] = seats_available

    cursor.close()
    conn.close()
    return render_template(
        "admin/edit_booking.html",
        booking=booking,
        max_tickets=max_tickets,
        statuses=BOOKING_STATUSES,
    )


@admin_required
def admin_delete_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT event_id FROM bookings WHERE booking_id=%s", (booking_id,))
        booking = cursor.fetchone()
        cursor.execute("DELETE FROM payments WHERE booking_id = %s", (booking_id,))
        cursor.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
        if booking:
            promote_waitlist_entries(cursor, conn, booking["event_id"])
        conn.commit()
        flash("Booking deleted.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_bookings"))


@admin_required
def admin_delete_user(user_id):
    next_url = get_safe_next_url() or url_for("admin_users")
    self_deleted = bool(g.current_user and g.current_user.get("user_id") == user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT user_id, full_name, email, role
        FROM users
        WHERE user_id = %s
        """,
        (user_id,),
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    try:
        active_event_ids = delete_user_account(cursor, user_id)
        for event_id in active_event_ids:
            promote_waitlist_entries(cursor, conn, event_id)
        conn.commit()
        cursor.close()
        conn.close()

        if self_deleted:
            session.clear()
            flash("Your account has been deleted.", "success")
            return redirect(url_for("login"))

        flash("User account deleted.", "success")
        return redirect(next_url)
    except mysql.connector.Error as err:
        conn.rollback()
        cursor.close()
        conn.close()
        flash(f"Database error: {err}", "error")
        return redirect(next_url)


@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    filters = []
    params = []

    if q:
        like = f"%{q}%"
        filters.append("(u.full_name LIKE %s OR u.email LIKE %s)")
        params.extend([like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        f"""
        SELECT u.user_id, u.full_name, u.email, u.phone, u.role, u.created_at,
               COUNT(b.booking_id) AS bookings_count,
               COALESCE(SUM(CASE WHEN COALESCE(b.status, 'Confirmed') <> 'Cancelled' THEN b.tickets ELSE 0 END), 0) AS tickets_total
        FROM users u
        LEFT JOIN bookings b ON b.user_id = u.user_id
        WHERE {where_clause}
        GROUP BY u.user_id, u.full_name, u.email, u.phone, u.role, u.created_at
        ORDER BY u.user_id DESC
        """,
        tuple(params),
    )
    users_rows = cursor.fetchall()
    pending_invitations = fetch_pending_role_invitations(cursor)
    for invitation in pending_invitations:
        expires_at = invitation.get("expires_at")
        invitation["is_expired"] = bool(expires_at and expires_at <= current_datetime())
        invitation["status_label"] = "Expired" if invitation["is_expired"] else "Pending"

    cursor.close()
    conn.close()

    return render_template(
        "admin/users.html",
        users=users_rows,
        pending_invitations=pending_invitations,
        q=q,
        role_options=[(ROLE_USER, "User"), (ROLE_ADMIN, "Admin")],
    )


@admin_required
def admin_send_user_role_invite_email():
    next_url = get_safe_next_url() or url_for("admin_users")
    email = request.form.get("invite_email", "").strip().lower()
    role = normalize_user_role(request.form.get("invite_role", ROLE_USER))

    if not email:
        flash("Please enter an email address.", "error")
        return redirect(next_url)

    if not is_valid_email(email):
        flash("Please enter a valid email address.", "error")
        return redirect(next_url)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    created_new_user = False
    token_hash = None

    try:
        user, created_new_user = fetch_or_create_invited_user(cursor, email)
        raw_token, expires_at, token_hash = create_role_invitation_request(
            cursor,
            user_id=user["user_id"],
            role=role,
            invited_by=g.current_user["user_id"],
        )
        conn.commit()

        invite_url = build_public_url("accept_role_invitation", token=raw_token)
        subject, body = build_access_invitation_email(user, invite_url, expires_at, role=role)
        send_email_message(subject, body, email)

        try:
            invalidate_other_role_invitation_tokens(cursor, user["user_id"], token_hash)
            conn.commit()
        except mysql.connector.Error as err:
            conn.rollback()
            app.logger.warning(
                "Role invite email sent to %s, but older invite tokens could not be invalidated: %s",
                email,
                err,
            )

        flash(
            f"Invitation sent to {email} for {role.title()} access. The link expires at {expires_at.strftime('%B %d, %Y %H:%M')}.",
            "success",
        )
        return redirect(next_url)
    except (RuntimeError, smtplib.SMTPException, OSError) as err:
        conn.rollback()
        try:
            if token_hash:
                delete_role_invitation_token(cursor, token_hash)
            if created_new_user:
                cursor.execute("DELETE FROM users WHERE user_id = %s", (user["user_id"],))
            conn.commit()
        except Exception:
            conn.rollback()
        flash(f"Unable to send the invitation email: {err}", "error")
        return redirect(next_url)
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        return redirect(next_url)
    finally:
        cursor.close()
        conn.close()


@admin_required
def admin_resend_user_role_invite_email(invitation_id):
    next_url = get_safe_next_url() or url_for("admin_users")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT t.invitation_id, t.user_id, t.role, t.used_at, t.expires_at,
               u.full_name, u.email
        FROM role_invitation_tokens t
        JOIN users u ON u.user_id = t.user_id
        WHERE t.invitation_id = %s
        LIMIT 1
        """,
        (invitation_id,),
    )
    invitation = cursor.fetchone()

    if not invitation:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if invitation.get("used_at"):
        cursor.close()
        conn.close()
        flash("This invitation has already been redeemed or revoked.", "error")
        return redirect(next_url)

    token_hash = None
    try:
        raw_token, expires_at, token_hash = create_role_invitation_request(
            cursor,
            user_id=invitation["user_id"],
            role=normalize_user_role(invitation.get("role")),
            invited_by=g.current_user["user_id"],
        )
        conn.commit()

        invite_url = build_public_url("accept_role_invitation", token=raw_token)
        subject, body = build_access_invitation_email(invitation, invite_url, expires_at, role=invitation.get("role"))
        send_email_message(subject, body, invitation["email"])

        try:
            invalidate_other_role_invitation_tokens(cursor, invitation["user_id"], token_hash)
            conn.commit()
        except mysql.connector.Error as err:
            conn.rollback()
            app.logger.warning(
                "Role invite resent to %s, but older invite tokens could not be invalidated: %s",
                invitation["email"],
                err,
            )

        flash(
            f"Invitation resent to {invitation['email']}. It now expires at {expires_at.strftime('%B %d, %Y %H:%M')}.",
            "success",
        )
        return redirect(next_url)
    except (RuntimeError, smtplib.SMTPException, OSError) as err:
        conn.rollback()
        try:
            if token_hash:
                delete_role_invitation_token(cursor, token_hash)
                conn.commit()
        except Exception:
            conn.rollback()
        flash(f"Unable to resend the invitation email: {err}", "error")
        return redirect(next_url)
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        return redirect(next_url)
    finally:
        cursor.close()
        conn.close()


@admin_required
def admin_revoke_user_role_invite(invitation_id):
    next_url = get_safe_next_url() or url_for("admin_users")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT invitation_id, user_id, used_at
        FROM role_invitation_tokens
        WHERE invitation_id = %s
        LIMIT 1
        """,
        (invitation_id,),
    )
    invitation = cursor.fetchone()

    if not invitation:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if invitation.get("used_at"):
        cursor.close()
        conn.close()
        flash("That invitation has already been redeemed or revoked.", "error")
        return redirect(next_url)

    try:
        cursor.execute(
            """
            UPDATE role_invitation_tokens
            SET used_at = %s
            WHERE invitation_id = %s AND used_at IS NULL
            """,
            (current_datetime(), invitation_id),
        )
        conn.commit()
        flash("Invitation revoked.", "success")
        return redirect(next_url)
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
        return redirect(next_url)
    finally:
        cursor.close()
        conn.close()


@admin_required
def admin_reset_user_password(user_id):
    next_url = get_safe_next_url() or url_for("admin_users")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT user_id, full_name, email, role, password_changed_at
        FROM users
        WHERE user_id = %s
        """,
        (user_id,),
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(new_password) < 8:
            flash("Password must be at least 8 characters long.", "error")
        elif new_password != confirm_password:
            flash("Passwords do not match.", "error")
        else:
            try:
                password_changed_at = current_datetime()
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash=%s, password_changed_at=%s
                    WHERE user_id=%s
                    """,
                    (generate_password_hash(new_password), password_changed_at, user_id),
                )
                invalidate_password_reset_tokens(cursor, user_id)
                conn.commit()
                if g.current_user and g.current_user.get("user_id") == user_id:
                    session[PASSWORD_VERSION_SESSION_KEY] = serialize_password_version(password_changed_at)
                flash("User password updated successfully.", "success")
                cursor.close()
                conn.close()
                return redirect(next_url)
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")

    cursor.close()
    conn.close()
    return render_template("admin/user_password_form.html", user=user)


@admin_required
def admin_send_user_password_reset_email(user_id):
    next_url = get_safe_next_url() or url_for("admin_reset_user_password", user_id=user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT user_id, full_name, email, role
        FROM users
        WHERE user_id = %s
        """,
        (user_id,),
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    email = (user.get("email") or "").strip().lower()
    if not is_valid_email(email):
        cursor.close()
        conn.close()
        flash("This user does not have a valid email address on file.", "error")
        return redirect(next_url)

    token_hash = None
    try:
        raw_token, expires_at, token_hash = create_password_reset_request(
            cursor,
            user_id=user_id,
            requested_by=g.current_user["user_id"],
        )
        conn.commit()

        reset_url = build_public_url("reset_password", token=raw_token)
        subject, body = build_password_reset_email(user, reset_url, expires_at)
        send_email_message(subject, body, email)

        try:
            invalidate_other_password_reset_tokens(cursor, user_id, token_hash)
            conn.commit()
        except mysql.connector.Error as err:
            conn.rollback()
            app.logger.warning(
                "Password reset email sent to %s, but older tokens could not be invalidated: %s",
                email,
                err,
            )

        flash(
            f"Password reset link sent to {email}. It expires at {expires_at.strftime('%B %d, %Y %H:%M')}.",
            "success",
        )
        return redirect(next_url)
    except (RuntimeError, smtplib.SMTPException, OSError) as err:
        conn.rollback()
        try:
            if token_hash:
                delete_password_reset_token(cursor, token_hash)
                conn.commit()
        except Exception:
            conn.rollback()
        flash(f"Unable to send the reset email: {err}", "error")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(next_url)


@admin_required
def admin_reviews():
    q = request.args.get("q", "").strip()
    status_raw = request.args.get("status", "").strip()
    status_filter = ""

    if status_raw:
        if status_raw in REVIEW_STATUSES:
            status_filter = status_raw
        else:
            flash("Invalid review status filter.", "error")

    page = parse_positive_int(request.args.get("page"), 1)
    per_page = 10

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    count_status = status_filter or None
    total_reviews = count_reviews(cursor, q=q, status=count_status)
    total_pages = max(1, (total_reviews + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    reviews = fetch_reviews(
        cursor,
        q=q,
        status=count_status,
        limit=per_page,
        offset=offset,
        order_by="r.created_at DESC, r.review_id DESC",
    )

    pagination_base_args = {}
    if q:
        pagination_base_args["q"] = q
    if status_filter:
        pagination_base_args["status"] = status_filter

    pagination_links = []
    for item in build_pagination_pages(page, total_pages):
        if item is None:
            pagination_links.append({"type": "ellipsis"})
        else:
            page_args = dict(pagination_base_args)
            page_args["page"] = item
            pagination_links.append(
                {
                    "type": "page",
                    "page": item,
                    "url": url_for("admin_reviews", **page_args),
                }
            )

    cursor.close()
    conn.close()

    return render_template(
        "admin/reviews.html",
        reviews=reviews,
        q=q,
        status=status_filter,
        statuses=REVIEW_STATUSES,
        page=page,
        total_pages=total_pages,
        total_reviews=total_reviews,
        start_item=((page - 1) * per_page + 1) if total_reviews else 0,
        end_item=min(page * per_page, total_reviews) if total_reviews else 0,
        pagination_links=pagination_links,
        pagination_prev_url=url_for("admin_reviews", page=page - 1, **pagination_base_args) if page > 1 else None,
        pagination_next_url=url_for("admin_reviews", page=page + 1, **pagination_base_args) if page < total_pages else None,
    )



@admin_required
def admin_contact_messages():
    q = request.args.get("q", "").strip()
    status_raw = request.args.get("status", "").strip()
    status_filter = ""

    if status_raw:
        if status_raw in CONTACT_MESSAGE_STATUSES:
            status_filter = status_raw
        else:
            flash("Invalid contact message status filter.", "error")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    messages = fetch_contact_messages(cursor, q=q, status=status_filter)

    cursor.close()
    conn.close()

    return render_template(
        "admin/messages.html",
        contact_messages=messages,
        q=q,
        status=status_filter,
        statuses=CONTACT_MESSAGE_STATUSES,
    )


@admin_required
def admin_reply_contact_message(message_id):
    next_url = get_safe_next_url() or url_for("admin_contact_messages")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    contact_message = fetch_contact_message(cursor, message_id)

    if not contact_message:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        admin_reply = request.form.get("admin_reply", "").strip()
        next_url = get_safe_next_url() or url_for("admin_contact_messages")

        if not admin_reply:
            flash("Reply cannot be empty.", "error")
            cursor.close()
            conn.close()
            return render_template(
                "admin/reply_contact_message.html",
                contact_message=contact_message,
                admin_reply_text=admin_reply,
                next_url=next_url,
            )

        try:
            now = current_datetime()
            cursor.execute(
                """
                UPDATE contact_messages
                SET admin_reply=%s, replied_by=%s, replied_at=%s, status=%s
                WHERE message_id=%s
                """,
                (
                    admin_reply,
                    g.current_user["user_id"],
                    now,
                    "Replied",
                    message_id,
                ),
            )
            conn.commit()
            flash("Reply saved. The user can view it on their profile.", "success")
            cursor.close()
            conn.close()
            return redirect(next_url)
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")
            cursor.close()
            conn.close()
            return render_template(
                "admin/reply_contact_message.html",
                contact_message=contact_message,
                admin_reply_text=admin_reply,
                next_url=next_url,
            )

    admin_reply_text = contact_message.get("admin_reply") or ""
    cursor.close()
    conn.close()
    return render_template(
        "admin/reply_contact_message.html",
        contact_message=contact_message,
        admin_reply_text=admin_reply_text,
        next_url=next_url,
    )


@admin_required
def admin_delete_contact_message(message_id):
    next_url = get_safe_next_url() or url_for("admin_contact_messages")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM contact_messages WHERE message_id = %s", (message_id,))
        conn.commit()
        flash("Contact message deleted.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(next_url)


@admin_required
def admin_approve_review(review_id):
    next_url = get_safe_next_url() or url_for("admin_reviews")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    review_rows = fetch_reviews(cursor, review_id=review_id, limit=1)

    if not review_rows:
        cursor.close()
        conn.close()
        flash("Review not found.", "error")
        return redirect(next_url)

    try:
        now = current_datetime()
        cursor.execute(
            """
            UPDATE reviews
            SET status=%s,
                reviewed_by=%s,
                reviewed_at=%s,
                updated_at=%s
            WHERE review_id=%s
            """,
            (
                REVIEW_STATUS_APPROVED,
                g.current_user["user_id"],
                now,
                now,
                review_id,
            ),
        )
        conn.commit()
        flash("Review approved and added to the public review list.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(next_url)


@admin_required
def admin_reject_review(review_id):
    next_url = get_safe_next_url() or url_for("admin_reviews")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    review_rows = fetch_reviews(cursor, review_id=review_id, limit=1)

    if not review_rows:
        cursor.close()
        conn.close()
        flash("Review not found.", "error")
        return redirect(next_url)

    try:
        now = current_datetime()
        cursor.execute(
            """
            UPDATE reviews
            SET status=%s,
                reviewed_by=%s,
                reviewed_at=%s,
                updated_at=%s
            WHERE review_id=%s
            """,
            (
                REVIEW_STATUS_REJECTED,
                g.current_user["user_id"],
                now,
                now,
                review_id,
            ),
        )
        conn.commit()
        flash("Review rejected.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(next_url)


@admin_required
def admin_delete_review(review_id):
    next_url = get_safe_next_url() or url_for("admin_reviews")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM reviews WHERE review_id = %s", (review_id,))
        if cursor.rowcount == 0:
            flash("Review not found.", "error")
        else:
            conn.commit()
            flash("Review deleted.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(next_url)


def contact():
    if request.method == "POST":
        if not g.current_user:
            flash("Please log in to send a message.", "error")
            return redirect(url_for("login", next=url_for("contact")))

        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        sender_name = (g.current_user.get("full_name") or "").strip()
        sender_email = (g.current_user.get("email") or "").strip()
        sender_phone = (g.current_user.get("phone") or "").strip()

        if not subject or not message:
            flash("Please complete the subject and message fields.", "error")
            return render_template(
                "contact.html",
                form_subject=subject,
                form_message=message,
            )

        if len(subject) > 255:
            flash("Subject must be 255 characters or fewer.", "error")
            return render_template(
                "contact.html",
                form_subject=subject[:255],
                form_message=message,
            )

        if len(message) > 5000:
            flash("Message must be 5000 characters or fewer.", "error")
            return render_template(
                "contact.html",
                form_subject=subject,
                form_message=message[:5000],
            )

        if not sender_name or not sender_email:
            flash("Your profile details are incomplete. Please update your account first.", "error")
            return render_template(
                "contact.html",
                form_subject=subject,
                form_message=message,
            )

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                INSERT INTO contact_messages (
                    user_id, sender_name, sender_email, sender_phone, subject, message, status, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    g.current_user["user_id"],
                    sender_name,
                    sender_email,
                    sender_phone,
                    subject,
                    message,
                    "New",
                    current_datetime(),
                ),
            )
            conn.commit()
            flash("Your message has been sent. You can view the reply on your profile.", "success")
            return redirect(url_for("contact"))
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")
            return render_template(
                "contact.html",
                form_subject=subject,
                form_message=message,
            )
        finally:
            cursor.close()
            conn.close()

    return render_template("contact.html", form_subject="", form_message="")


def login():
    if g.current_user:
        next_url = get_safe_next_url()
        return redirect(next_url or default_login_redirect())

    next_url = get_safe_next_url()
    email_prefill = (request.args.get("email") or "").strip().lower()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Please enter your email and password.", "error")
            return redirect(url_for("login", next=next_url))

        if not is_valid_email(email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("login", next=next_url))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT user_id, full_name, email, phone, role, password_hash, password_changed_at
            FROM users
            WHERE email = %s
            """,
            (email,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user or not user.get("password_hash") or not check_password_hash(
            user["password_hash"], password
        ):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login", next=next_url))

        session.clear()
        session["user_id"] = user["user_id"]
        session["user_role"] = user["role"]
        session["user_name"] = user["full_name"]
        session[PASSWORD_VERSION_SESSION_KEY] = serialize_password_version(user.get("password_changed_at"))
        flash("Login successful.", "success")

        destination = next_url
        if not destination:
            destination = url_for("admin_dashboard") if user["role"] == ROLE_ADMIN else url_for("home")
        return redirect(destination)

    return render_template("login.html", next=next_url, email=email_prefill)


def register():
    if g.current_user:
        return redirect(default_login_redirect())

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not full_name or not email or not password:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("register"))

        if not is_valid_email(email):
            flash("Please enter a valid email address.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT user_id, password_hash, role
            FROM users
            WHERE email = %s
            """,
            (email,),
        )
        existing = cursor.fetchone()

        try:
            now = current_datetime()
            password_hash = generate_password_hash(password)
            if existing:
                if existing.get("password_hash"):
                    flash("An account with that email already exists.", "error")
                    return redirect(url_for("register"))

                password_changed_at = current_datetime()
                cursor.execute(
                    """
                    UPDATE users
                    SET full_name=%s, phone=%s, password_hash=%s, password_changed_at=%s, role=%s, created_at=%s
                    WHERE user_id=%s
                    """,
                    (
                        full_name,
                        phone,
                        password_hash,
                        password_changed_at,
                        existing.get("role") or ROLE_USER,
                        now,
                        existing["user_id"],
                    ),
                )
                invalidate_password_reset_tokens(cursor, existing["user_id"])
            else:
                cursor.execute(
                    """
                    INSERT INTO users (full_name, email, phone, password_hash, password_changed_at, role, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (full_name, email, phone, password_hash, now, ROLE_USER, now),
                )

            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")
        finally:
            cursor.close()
            conn.close()

    return render_template("register.html")


def logout():
    if session.get("user_id"):
        session.clear()
        flash("You have been logged out.", "success")
    return redirect(url_for("home"))


def accept_role_invitation(token):
    token = (token or "").strip()
    invalid_reason = "This invitation link is invalid or has expired."

    if not token:
        return render_template(
            "invite_setup.html",
            invite_request=None,
            token="",
            invalid_reason=invalid_reason,
        ), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    invite_request = fetch_role_invitation_request(cursor, token)

    if (
        not invite_request
        or invite_request.get("used_at")
        or invite_request.get("expires_at") <= current_datetime()
    ):
        cursor.close()
        conn.close()
        return render_template(
            "invite_setup.html",
            invite_request=None,
            token=token,
            invalid_reason=invalid_reason,
        ), 400

    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(new_password) < 8:
            flash("Password must be at least 8 characters long.", "error")
        elif new_password != confirm_password:
            flash("Passwords do not match.", "error")
        else:
            try:
                invited_role = normalize_user_role(invite_request.get("role"))
                password_changed_at = current_datetime()
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash=%s, password_changed_at=%s, role=%s
                    WHERE user_id=%s
                    """,
                    (
                        generate_password_hash(new_password),
                        password_changed_at,
                        invited_role,
                        invite_request["user_id"],
                    ),
                )
                invalidate_password_reset_tokens(cursor, invite_request["user_id"])
                invalidate_role_invitation_tokens(cursor, invite_request["user_id"])
                conn.commit()

                clear_auth_session()
                session["user_id"] = invite_request["user_id"]
                session["user_role"] = invited_role
                session["user_name"] = invite_request["full_name"]
                session[PASSWORD_VERSION_SESSION_KEY] = serialize_password_version(password_changed_at)
                flash("Your access has been activated successfully.", "success")

                cursor.close()
                conn.close()
                destination = url_for("admin_dashboard") if invited_role == ROLE_ADMIN else url_for("home")
                return redirect(destination)
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")

    cursor.close()
    conn.close()
    return render_template(
        "invite_setup.html",
        invite_request=invite_request,
        token=token,
        invalid_reason="",
    )


def reset_password(token):
    token = (token or "").strip()
    invalid_reason = "This password reset link is invalid or has expired."

    if not token:
        return render_template(
            "password_reset.html",
            reset_request=None,
            token="",
            invalid_reason=invalid_reason,
        ), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    reset_request = fetch_password_reset_request(cursor, token)

    if (
        not reset_request
        or reset_request.get("used_at")
        or reset_request.get("expires_at") <= current_datetime()
    ):
        cursor.close()
        conn.close()
        return render_template(
            "password_reset.html",
            reset_request=None,
            token=token,
            invalid_reason=invalid_reason,
        ), 400

    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(new_password) < 8:
            flash("Password must be at least 8 characters long.", "error")
        elif new_password != confirm_password:
            flash("Passwords do not match.", "error")
        else:
            try:
                password_changed_at = current_datetime()
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash=%s, password_changed_at=%s
                    WHERE user_id=%s
                    """,
                    (
                        generate_password_hash(new_password),
                        password_changed_at,
                        reset_request["user_id"],
                    ),
                )
                invalidate_password_reset_tokens(cursor, reset_request["user_id"])
                conn.commit()
                clear_auth_session()
                flash(
                    "Your password has been reset successfully. Please log in with the new password.",
                    "success",
                )
                cursor.close()
                conn.close()
                return redirect(url_for("login", email=reset_request["email"]))
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")

    cursor.close()
    conn.close()
    return render_template(
        "password_reset.html",
        reset_request=reset_request,
        token=token,
        invalid_reason="",
    )


@login_required
def edit_booking(booking_id):
    if g.current_user.get("role") == ROLE_ADMIN:
        return admin_edit_booking(booking_id)
    return user_edit_booking(booking_id)


@login_required
def delete_booking(booking_id):
    if g.current_user.get("role") == ROLE_ADMIN:
        return admin_delete_booking(booking_id)
    return cancel_booking(booking_id)


def forbidden(_e):
    return render_template("403.html"), 403


def page_not_found(_e):
    return render_template("404.html"), 404


def unauthorized(_e):
    return render_template("401.html"), 401


def internal_error(_e):
    return render_template("500.html"), 500


# Route registration lives in blueprint modules to keep the view code grouped by area.
from blueprints.public import bp as public_bp
from blueprints.account import bp as account_bp
from blueprints.auth import bp as auth_bp
from blueprints.admin import bp as admin_bp
from blueprints.errors import bp as errors_bp

for blueprint in (public_bp, account_bp, auth_bp, admin_bp, errors_bp):
    app.register_blueprint(blueprint)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
