"""Flask application for Bristol Community Events.

The project stays in one file for coursework convenience, but the code is
grouped into clear sections: constants, helpers, database setup, route
handlers, and error pages.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
import re

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

from config import (
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_NAME,
    DEFAULT_ADMIN_PASSWORD,
    SECRET_KEY,
)
from dbfunc import get_db_connection
from receipt import booking_receipt_reference, build_booking_receipt_pdf as _build_booking_receipt_pdf
from seed_data import DEFAULT_REVIEWS, DEFAULT_VENUES

app = Flask(__name__)
app.secret_key = SECRET_KEY

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
REFUND_WINDOW_HOURS = 72
REFUND_PROCESSING_WORKING_DAYS = 2
ACTIVE_BOOKING_CONDITION = "COALESCE(status, 'Confirmed') <> 'Cancelled'"

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
    return event_date - timedelta(days=3)


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


def compute_booking_amounts(price, tickets: int, is_student: bool):
    unit_price = to_money(price)
    subtotal = (unit_price * tickets).quantize(Decimal("0.01"))
    discount = Decimal("0.00")
    if is_student:
        discount = (subtotal * Decimal("0.10")).quantize(Decimal("0.01"))
    total = (subtotal - discount).quantize(Decimal("0.01"))
    return subtotal, discount, total


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
        "SELECT venue_id, venue_name, address, city, capacity FROM venues ORDER BY venue_name"
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
        filters.append("(v.venue_name LIKE %s OR v.address LIKE %s OR v.city LIKE %s)")
        params.extend([like, like, like])

    where_clause = " AND ".join(filters) if filters else "1=1"
    limit_clause = ""
    if limit is not None:
        limit_clause = f" LIMIT {int(limit)}"
        if offset is not None:
            limit_clause += f" OFFSET {int(offset)}"

    cursor.execute(
        f"""
        SELECT v.venue_id, v.venue_name, v.address, v.city, v.capacity,
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
        ORDER BY v.venue_name ASC
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

    cursor.execute(
        """
        SELECT e.event_id, e.event_name, e.event_date, e.location, e.price,
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
    events = cursor.fetchall()
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
            capacity INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def seed_default_venues(cursor):
    """Seed demo venues when the venues table is empty."""
    cursor.execute("SELECT COUNT(*) AS count FROM venues")
    venue_count = cursor.fetchone()["count"] or 0
    if venue_count > 0:
        return

    for seed in DEFAULT_VENUES:
        cursor.execute(
            """
            INSERT INTO venues (venue_name, address, city, capacity)
            VALUES (%s, %s, %s, %s)
            """,
            (
                seed["venue_name"][:150],
                seed["address"][:255],
                seed["city"][:100],
                int(seed.get("capacity") or 0),
            ),
        )


def ensure_venues_table(cursor):
    if not table_exists(cursor, "venues"):
        create_venues_table(cursor)
    else:
        add_column_if_missing(cursor, "venues", "address", "VARCHAR(255) NULL AFTER venue_name")
        add_column_if_missing(cursor, "venues", "city", "VARCHAR(100) NULL AFTER address")
        add_column_if_missing(
            cursor,
            "venues",
            "capacity",
            "INT NOT NULL DEFAULT 0 AFTER city",
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


def initialize_database():
    """Apply lightweight migrations and seed demo data once per process."""
    global _db_initialized
    if _db_initialized:
        return

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        add_column_if_missing(cursor, "users", "password_hash", "VARCHAR(255) NULL AFTER email")
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
        ensure_venues_table(cursor)
        ensure_event_venue_foreign_key(cursor)
        ensure_contact_messages_table(cursor)
        ensure_reviews_table(cursor)
        ensure_newsletter_subscribers_table(cursor)

        cursor.execute("UPDATE users SET role=%s WHERE role IS NULL OR role=''", (ROLE_USER,))
        cursor.execute(
            "UPDATE bookings SET status='Confirmed' WHERE status IS NULL OR status=''"
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
        SELECT user_id, password_hash, role
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

        if updates:
            cursor.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE user_id=%s",
                tuple(params + [admin_user["user_id"]]),
            )
        return

    cursor.execute(
        """
        INSERT INTO users (full_name, email, phone, password_hash, role, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            DEFAULT_ADMIN_NAME,
            DEFAULT_ADMIN_EMAIL,
            "",
            generate_password_hash(DEFAULT_ADMIN_PASSWORD),
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
        SELECT user_id, full_name, email, phone, role, created_at
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

    session["user_role"] = user["role"]
    session["user_name"] = user["full_name"]
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


@app.context_processor
def inject_template_globals():
    current_user = getattr(g, "current_user", None)
    return {
        "current_user": current_user,
        "is_authenticated": current_user is not None,
        "is_admin": bool(current_user and current_user.get("role") == ROLE_ADMIN),
        "newsletter_subscribers": getattr(g, "newsletter_subscribers", []),
        "newsletter_subscriber_count": getattr(g, "newsletter_subscriber_count", 0),
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
        SELECT b.booking_id, b.user_id, b.event_id, b.booking_date, b.created_at,
               {booked_at_sql} AS booked_at,
               b.tickets, b.is_student, b.discount_applied,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email, u.phone, u.role,
               e.event_name, e.event_date, e.location, e.price, e.event_capacity,
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
               b.tickets, b.is_student, b.discount_applied,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_id, e.event_name, e.event_date,
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


def can_access_booking(booking) -> bool:
    if not booking or not g.current_user:
        return False
    if g.current_user.get("role") == ROLE_ADMIN:
        return True
    return booking.get("user_id") == g.current_user.get("user_id")


def payment_status_for_booking(status: str) -> str:
    if status == "Cancelled":
        return "Cancelled"
    if status == "Pending":
        return "Pending"
    return "Paid"



def build_booking_receipt_pdf(booking):
    """Delegate PDF rendering to the receipt helper module."""
    return _build_booking_receipt_pdf(booking)


def default_login_redirect():
    if g.current_user and g.current_user.get("role") == ROLE_ADMIN:
        return url_for("admin_dashboard")
    return url_for("home")


def view_events():
    return redirect(url_for("events"))


def build_event_listing_filters(category="", q="", date_filter="", price_filter=""):
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

    if price_filter == "free":
        filters.append("(e.price IS NULL OR e.price = 0)")
    elif price_filter == "paid":
        filters.append("(e.price IS NOT NULL AND e.price > 0)")

    return filters, params, price_filter


def fetch_event_listing(category="", q="", date_filter="", price_filter="", limit=None, offset=None):
    filters, params, price_filter = build_event_listing_filters(
        category=category,
        q=q,
        date_filter=date_filter,
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
        ORDER BY
            CASE WHEN e.event_date >= CURDATE() THEN 0 ELSE 1 END,
            CASE WHEN e.event_date >= CURDATE() THEN e.event_date END ASC,
            CASE WHEN e.event_date < CURDATE() THEN e.event_date END DESC
        {limit_clause}
        """,
        tuple(params),
    )

    events_rows = cursor.fetchall()

    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()

    cursor.close()
    conn.close()

    return events_rows, categories, price_filter


def count_event_listing(category="", q="", date_filter="", price_filter=""):
    filters, params, _ = build_event_listing_filters(
        category=category,
        q=q,
        date_filter=date_filter,
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
    booking_event["max_tickets"] = booking_ticket_limit(booking_event)
    booking_event["event_date_label"] = (
        booking_event["event_date"].strftime("%B %d, %Y")
        if booking_event.get("event_date")
        else ""
    )
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


def process_booking_submission(cursor, conn, event, event_id, invalid_redirect):
    booked_at = current_datetime()
    today = booked_at.date()
    tickets = parse_ticket_count(request.form.get("tickets"))
    phone = request.form.get("phone", "").strip()
    is_student = "is_student" in request.form
    payment_method = request.form.get("payment_method", "card").strip().lower()

    if tickets is None:
        flash("Tickets must be between 1 and 10.", "error")
        return redirect(invalid_redirect)

    if payment_method not in PAYMENT_METHODS:
        flash("Please choose a valid payment method.", "error")
        return redirect(invalid_redirect)

    if event.get("event_date") and event["event_date"] < today:
        flash("You cannot book an event that has already taken place.", "error")
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

    _, discount, total_amount = compute_booking_amounts(
        event.get("price"), tickets, is_student
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
                user_id, event_id, booking_date, tickets, is_student,
                discount_applied, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.current_user["user_id"],
                event_id,
                today,
                tickets,
                is_student,
                discount,
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
                total_amount,
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
    phone = request.form.get("phone", "").strip()
    is_student = "is_student" in request.form
    payment_method = normalize_payment_method(request.form.get("payment_method"))

    if tickets is None:
        flash("Tickets must be between 1 and 10.", "error")
        return redirect(invalid_redirect)

    if not payment_method:
        flash("Please choose a valid payment method.", "error")
        return redirect(invalid_redirect)

    if event.get("event_date") and event["event_date"] < today:
        flash("You cannot book an event that has already taken place.", "error")
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

    subtotal, discount, total_amount = compute_booking_amounts(
        event.get("price"), tickets, is_student
    )

    session[PENDING_BOOKING_SESSION_KEY] = {
        "event_id": int(event_id),
        "return_url": invalid_redirect,
        "event_name": event.get("event_name") or "",
        "event_date": event["event_date"].isoformat() if event.get("event_date") else "",
        "event_date_label": (
            event["event_date"].strftime("%B %d, %Y") if event.get("event_date") else ""
        ),
        "venue_name": event.get("venue_name") or "",
        "location": event.get("location") or "",
        "price": str(to_money(event.get("price"))),
        "tickets": tickets,
        "phone": phone,
        "is_student": bool(is_student),
        "payment_method": payment_method,
        "payment_method_label": payment_method_label(payment_method),
        "booking_date": booked_at.isoformat(timespec="seconds"),
        "subtotal": str(subtotal),
        "discount": str(discount),
        "total_amount": str(total_amount),
        "remaining_seats": event.get("remaining_seats"),
        "max_tickets": booking_ticket_limit(event),
    }

    return redirect(url_for("payment_details"))


def create_booking_from_pending(cursor, conn, pending_booking, payment_method, payment_source):
    event_id = int(pending_booking.get("event_id") or 0)
    tickets = parse_positive_int(pending_booking.get("tickets"), 1)
    is_student = bool(pending_booking.get("is_student"))
    phone = (pending_booking.get("phone") or "").strip()
    return_url = pending_booking.get("return_url") or url_for("book_tickets")
    booked_at = current_datetime()
    today = booked_at.date()

    event = fetch_event(cursor, event_id)
    if not event:
        flash("The selected event could not be found. Please start again.", "error")
        return None, redirect(return_url)

    if event.get("event_date") and event["event_date"] < today:
        flash("You cannot book an event that has already taken place.", "error")
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

    subtotal, discount, total_amount = compute_booking_amounts(
        event.get("price"), tickets, is_student
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
                user_id, event_id, booking_date, tickets, is_student,
                discount_applied, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.current_user["user_id"],
                event_id,
                today,
                tickets,
                is_student,
                discount,
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
                total_amount,
                payment_method,
                payment_source,
                "Paid",
                booked_at,
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
        SELECT e.event_id, e.event_name, e.event_date, e.location, e.price, e.event_capacity,
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
        ORDER BY e.event_date DESC, e.event_id DESC
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
def add_event():
    event = {}
    if request.method == "POST":
        event_name = request.form.get("event_name", "").strip()
        location = request.form.get("location", "").strip()
        event_date = parse_event_date(request.form.get("event_date"))
        price = parse_price(request.form.get("price"))
        capacity_raw = request.form.get("event_capacity", "").strip()
        event_capacity = parse_capacity(capacity_raw)
        venue_id_raw = request.form.get("venue_id")
        category_id_raw = request.form.get("category_id")

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
            "price": price,
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
            or not venue_id
            or not category_id
        ):
            flash("Please fill in all required fields (valid date/price).", "error")
        elif capacity_raw and event_capacity is None:
            flash("Capacity must be a number between 1 and 10000.", "error")
        elif event_date < today:
            flash("Event date cannot be in the past.", "error")
        else:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
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
                        cursor.execute(
                            """
                            INSERT INTO events (event_name, event_date, location, price, venue_id, category_id, event_capacity)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                event_name,
                                event_date,
                                location,
                                price,
                                venue_id,
                                category_id,
                                event_capacity,
                            ),
                        )
                        conn.commit()
                        flash("Event added successfully.", "success")
                        return redirect(url_for("admin_events"))
            except mysql.connector.Error as err:
                conn.rollback()
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

    cursor.execute("SELECT * FROM events WHERE event_id=%s", (event_id,))
    event = cursor.fetchone()
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
            or not venue_id
            or not category_id
        ):
            flash("Please fill in all required fields (valid date/price).", "error")
        elif capacity_raw and event_capacity is None:
            flash("Capacity must be a number between 1 and 10000.", "error")
        elif event_date < today and event_date != event.get("event_date"):
            flash("Event date cannot be in the past.", "error")
        else:
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
                        cursor.execute(
                            """
                            UPDATE events
                            SET event_name=%s, event_date=%s, location=%s, price=%s, venue_id=%s, category_id=%s, event_capacity=%s
                            WHERE event_id=%s
                            """,
                            (
                                event_name,
                                event_date,
                                location,
                                price,
                                venue_id,
                                category_id,
                                event_capacity,
                                event_id,
                            ),
                        )
                        conn.commit()
                        flash("Event updated successfully.", "success")
                        cursor.close()
                        conn.close()
                        return redirect(url_for("admin_events"))
            except mysql.connector.Error as err:
                conn.rollback()
                flash(f"Database error: {err}", "error")

        event.update(
            {
                "event_name": event_name,
                "location": location,
                "event_date": event_date,
                "price": price,
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
    price_filter = request.args.get("price", "").strip().lower()
    page = parse_positive_int(request.args.get("page"), 1)
    per_page = 6

    total_events = count_event_listing(
        category=category,
        q=q,
        date_filter=date_filter,
        price_filter=price_filter,
    )
    total_pages = max(1, (total_events + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    events_rows, categories, price_filter = fetch_event_listing(
        category=category,
        q=q,
        date_filter=date_filter,
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

    return render_template("event_details.html", event=event)


@login_required
def book(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    event = fetch_event(cursor, event_id)

    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        response = prepare_payment_details_session(
            cursor,
            conn,
            enrich_booking_event(event),
            event_id,
            url_for("book", event_id=event_id),
        )
        cursor.close()
        conn.close()
        return response

    cursor.close()
    conn.close()
    return render_template(
        "booking.html",
        event=enrich_booking_event(event),
        booking_events=[],
        today=current_date(),
        booking_now=current_datetime(),
    )


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
               b.tickets, b.is_student,
               b.discount_applied, COALESCE(b.status, 'Confirmed') AS status,
               e.event_id, e.event_name, e.event_date, e.location,
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
    cursor.close()
    conn.close()
    return render_template("my_bookings.html", bookings=bookings)


@login_required
def account():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT user_id, full_name, email, phone, role, created_at, password_hash
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
                    cursor.execute(
                        """
                        UPDATE users
                        SET password_hash=%s
                        WHERE user_id=%s
                        """,
                        (generate_password_hash(new_password), account_user["user_id"]),
                    )
                    conn.commit()
                    flash("Password changed successfully.", "success")
                    cursor.close()
                    conn.close()
                    return redirect(url_for("account"))
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

    cursor.close()
    conn.close()

    return render_template(
        "account.html",
        account_user=account_user,
        contact_messages=contact_messages,
        user_reviews=user_reviews,
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
    refund_allowed, refund_deadline = refund_is_allowed(booking.get("event_date"))
    refund_deadline_label = (
        refund_deadline.strftime("%B %d, %Y") if refund_deadline else ""
    )

    refund_heading = ""
    refund_message = ""
    if outcome == "approved":
        refund_heading = "Refund approved"
        refund_message = (
            f"Your booking has been cancelled. The refund will be processed within "
            f"{REFUND_PROCESSING_WORKING_DAYS} working days."
        )
    elif outcome == "not_allowed":
        refund_heading = "Refund not allowed"
        if refund_deadline_label:
            refund_message = (
                f"Refunds are available until {refund_deadline_label} for this event. "
                f"This booking is now outside the 3-day refund window."
            )
        else:
            refund_message = "This booking is outside the 3-day refund window."
    elif outcome == "already_cancelled":
        refund_heading = "Booking already cancelled"
        refund_message = "This booking was already cancelled before this refund check."

    return {
        "refund_allowed": refund_allowed,
        "refund_deadline": refund_deadline,
        "refund_deadline_label": refund_deadline_label,
        "refund_heading": refund_heading,
        "refund_message": refund_message,
        "refund_account": describe_refund_account(booking),
        "refund_processing_days": REFUND_PROCESSING_WORKING_DAYS,
        "refund_window_hours": REFUND_WINDOW_HOURS,
    }


def refund_policy():
    return render_template(
        "refund_policy.html",
        booking=None,
        refund_outcome="",
        refund_heading="Refund Policy",
        refund_message="Refunds are available up to 3 days before the event and are processed within 2 working days.",
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
        payment_status = (booking.get("payment_status") or "").strip().lower()
        if booking_status == "Cancelled" and payment_status.startswith("refund"):
            outcome = "approved"
        elif booking_status == "Cancelled":
            outcome = "already_cancelled"

    context = refund_status_context(booking, outcome)
    if not context["refund_heading"]:
        context["refund_heading"] = "Refund Policy"
        context["refund_message"] = (
            "Refunds are allowed up to 3 days before the event and are processed within 2 working days."
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
        refund_allowed, _refund_deadline = refund_is_allowed(booking.get("event_date"))
        if not refund_allowed:
            flash(
                "Refund not allowed. Cancellations must be made at least 3 days before the event.",
                "error",
            )
            cursor.close()
            conn.close()
            return redirect(url_for("booking_refund", booking_id=booking_id, outcome="not_allowed"))

        cursor.execute(
            "UPDATE bookings SET status=%s WHERE booking_id=%s",
            ("Cancelled", booking_id),
        )
        cursor.execute(
            """
            UPDATE payments
            SET payment_status=%s
            WHERE booking_id=%s
            """,
            ("Refund Approved", booking_id),
        )
        conn.commit()
        flash("Refund approved. Your booking has been cancelled.", "success")
        cursor.close()
        conn.close()
        return redirect(url_for("booking_refund", booking_id=booking_id, outcome="approved"))
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
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE COALESCE(payment_status, 'Paid') = 'Paid'
        """
    )
    revenue_total = cursor.fetchone()["total"] or 0

    messages_count = count_contact_messages(cursor)
    new_messages_count = count_contact_messages(cursor, status="New")
    reviews_count = count_reviews(cursor)
    pending_reviews_count = count_reviews(cursor, status=REVIEW_STATUS_PENDING)
    approved_reviews_count = count_reviews(cursor, status=REVIEW_STATUS_APPROVED)

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
        recent_bookings=recent_bookings,
        messages_count=messages_count,
        new_messages_count=new_messages_count,
        recent_messages=recent_messages,
        reviews_count=reviews_count,
        pending_reviews_count=pending_reviews_count,
        approved_reviews_count=approved_reviews_count,
        recent_reviews=recent_reviews,
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
        capacity_raw = request.form.get("capacity", "").strip()

        try:
            capacity = int(capacity_raw)
        except (TypeError, ValueError):
            capacity = None

        venue = {
            "venue_name": venue_name,
            "address": address,
            "city": city,
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
                        INSERT INTO venues (venue_name, address, city, capacity)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            venue_name[:150],
                            address[:255] or None,
                            city[:100] or None,
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
                            SET venue_name=%s, address=%s, city=%s, capacity=%s
                            WHERE venue_id=%s
                            """,
                            (
                                venue_name[:150],
                                address[:255] or None,
                                city[:100] or None,
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
               b.tickets, b.is_student, b.discount_applied,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_id, e.event_name, e.event_date,
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


@admin_required
def admin_edit_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        tickets = parse_ticket_count(request.form.get("tickets"))
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

        if status != "Cancelled":
            remaining = available_seats(cursor, booking["event_id"], exclude_booking_id=booking_id)
            if remaining is not None and tickets > remaining:
                cursor.close()
                conn.close()
                flash(f"Only {remaining} seat(s) are available for this event.", "error")
                return redirect(url_for("admin_edit_booking", booking_id=booking_id))

        _, discount, total_amount = compute_booking_amounts(
            booking.get("price"), tickets, is_student
        )
        payment_status = payment_status_for_booking(status)

        try:
            now = current_datetime()
            cursor.execute(
                """
                UPDATE bookings
                SET tickets=%s, is_student=%s, discount_applied=%s, status=%s
                WHERE booking_id=%s
                """,
                (tickets, is_student, discount, status, booking_id),
            )

            if booking.get("payment_id"):
                cursor.execute(
                    """
                    UPDATE payments
                    SET amount=%s, payment_status=%s, payment_date=%s
                    WHERE booking_id=%s
                    """,
                    (total_amount, payment_status, now, booking_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO payments (booking_id, amount, payment_method, payment_status, payment_date)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        booking_id,
                        total_amount,
                        "card",
                        payment_status,
                        now,
                    ),
                )

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
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM payments WHERE booking_id = %s", (booking_id,))
        cursor.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
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

    cursor.close()
    conn.close()

    return render_template("admin/users.html", users=users_rows, q=q)


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
            SELECT user_id, full_name, email, phone, role, password_hash
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
        flash("Login successful.", "success")

        destination = next_url
        if not destination:
            destination = url_for("admin_dashboard") if user["role"] == ROLE_ADMIN else url_for("home")
        return redirect(destination)

    return render_template("login.html", next=next_url)


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

                cursor.execute(
                    """
                    UPDATE users
                    SET full_name=%s, phone=%s, password_hash=%s, role=%s, created_at=%s
                    WHERE user_id=%s
                    """,
                    (
                        full_name,
                        phone,
                        password_hash,
                        existing.get("role") or ROLE_USER,
                        now,
                        existing["user_id"],
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO users (full_name, email, phone, password_hash, role, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (full_name, email, phone, password_hash, ROLE_USER, now),
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


@login_required
def edit_booking(booking_id):
    if g.current_user.get("role") != ROLE_ADMIN:
        abort(403)
    return admin_edit_booking(booking_id)


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
