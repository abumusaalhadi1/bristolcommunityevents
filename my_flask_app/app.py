from datetime import datetime
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

app = Flask(__name__)
app.secret_key = SECRET_KEY

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

ROLE_ADMIN = "admin"
ROLE_USER = "user"
BOOKING_STATUSES = ("Pending", "Confirmed", "Cancelled")
PAYMENT_METHODS = {"paypal", "card", "bank"}
ACTIVE_BOOKING_CONDITION = "COALESCE(status, 'Confirmed') <> 'Cancelled'"

_db_initialized = False


def parse_ticket_count(value):
    try:
        tickets = int(value)
    except (TypeError, ValueError):
        return None

    if 1 <= tickets <= 10:
        return tickets
    return None


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


def fetch_venues_and_categories(cursor):
    cursor.execute("SELECT venue_id, venue_name FROM venues ORDER BY venue_name")
    venues = cursor.fetchall()
    cursor.execute(
        "SELECT category_id, category_name FROM categories ORDER BY category_name"
    )
    categories = cursor.fetchall()
    return venues, categories


def venue_exists(cursor, venue_id: int) -> bool:
    cursor.execute("SELECT 1 FROM venues WHERE venue_id=%s", (venue_id,))
    return cursor.fetchone() is not None


def category_exists(cursor, category_id: int) -> bool:
    cursor.execute("SELECT 1 FROM categories WHERE category_id=%s", (category_id,))
    return cursor.fetchone() is not None


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return cursor.fetchone() is not None


def add_column_if_missing(cursor, table_name: str, column_name: str, definition: str):
    if not column_exists(cursor, table_name, column_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def initialize_database():
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

        cursor.execute("UPDATE users SET role=%s WHERE role IS NULL OR role=''", (ROLE_USER,))
        cursor.execute(
            "UPDATE bookings SET status='Confirmed' WHERE status IS NULL OR status=''"
        )

        bootstrap_default_admin(cursor)
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
        INSERT INTO users (full_name, email, phone, password_hash, role)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            DEFAULT_ADMIN_NAME,
            DEFAULT_ADMIN_EMAIL,
            "",
            generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            ROLE_ADMIN,
        ),
    )


def load_current_user():
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


@app.before_request
def prepare_request_context():
    initialize_database()
    load_current_user()


@app.context_processor
def inject_template_globals():
    current_user = getattr(g, "current_user", None)
    return {
        "current_user": current_user,
        "is_authenticated": current_user is not None,
        "is_admin": bool(current_user and current_user.get("role") == ROLE_ADMIN),
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
    cursor.execute(
        """
        SELECT b.booking_id, b.user_id, b.event_id, b.booking_date, b.created_at,
               b.tickets, b.is_student, b.discount_applied,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email, u.phone, u.role,
               e.event_name, e.event_date, e.location, e.price, e.event_capacity,
               v.venue_name, v.address,
               c.category_name,
               p.payment_id, p.amount, p.payment_method,
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


def default_login_redirect():
    if g.current_user and g.current_user.get("role") == ROLE_ADMIN:
        return url_for("admin_dashboard")
    return url_for("home")


@app.route("/view")
def view_events():
    return redirect(url_for("events"))


def fetch_event_listing(category="", q="", date_filter="", price_filter=""):
    price_filter = (price_filter or "").strip().lower()
    if price_filter not in {"", "free", "paid"}:
        price_filter = ""

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

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

    where_clause = " AND ".join(filters) if filters else "1=1"
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
        """,
        tuple(params),
    )

    events_rows = cursor.fetchall()

    cursor.execute("SELECT * FROM categories ORDER BY category_name")
    categories = cursor.fetchall()

    cursor.close()
    conn.close()

    return events_rows, categories, price_filter


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
    today = datetime.now().date()
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
    booked_at = datetime.now()
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
                discount_applied, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                g.current_user["user_id"],
                event_id,
                today,
                tickets,
                is_student,
                discount,
                "Confirmed",
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


@app.route("/admin/events")
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


@app.route("/add", methods=["GET", "POST"])
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

        today = datetime.now().date()
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


@app.route("/update/<int:event_id>", methods=["GET", "POST"])
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

        today = datetime.now().date()
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


@app.route("/delete/<int:event_id>", methods=["GET", "POST"])
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


@app.route("/")
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

    try:
        cursor.execute("SELECT * FROM testimonials ORDER BY created_at DESC LIMIT 4")
        testimonials = cursor.fetchall()
        for testimonial in testimonials:
            author_name = testimonial.get("author_name") or "Community Guest"
            initials = testimonial.get("author_initials")
            if not initials:
                initials = "".join(part[0] for part in author_name.split()[:2]).upper()
            testimonial["author_name"] = author_name
            testimonial["author_initials"] = initials or "CG"
            testimonial["rating"] = int(testimonial.get("rating") or 5)
    except mysql.connector.Error:
        testimonials = []

    cursor.close()
    conn.close()

    return render_template(
        "index.html",
        events_count=events_count,
        venues_count=venues_count,
        attendees_count=total_tickets,
        categories=categories,
        featured_events=featured_events,
        testimonials=testimonials,
    )


@app.route("/events")
def events():
    category = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()
    date_filter = request.args.get("date", "").strip()
    price_filter = request.args.get("price", "").strip().lower()
    events_rows, categories, price_filter = fetch_event_listing(
        category=category,
        q=q,
        date_filter=date_filter,
        price_filter=price_filter,
    )
    return render_template(
        "events.html",
        events=events_rows,
        categories=categories,
        q=q,
        category=category,
        date=date_filter,
        price=price_filter,
    )


@app.route("/book-tickets", methods=["GET", "POST"])
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

        response = process_booking_submission(
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
        today=datetime.now().date(),
        booking_now=datetime.now(),
    )


@app.route("/event/<int:event_id>")
def event_detail(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    event = fetch_event(cursor, event_id)
    cursor.close()
    conn.close()

    if not event:
        return render_template("404.html"), 404

    return render_template("event_details.html", event=event)


@app.route("/book/<int:event_id>", methods=["GET", "POST"])
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
        response = process_booking_submission(
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
        today=datetime.now().date(),
        booking_now=datetime.now(),
    )


@app.route("/bookings")
@login_required
def bookings_list():
    return redirect(url_for("my_bookings"))


@app.route("/my-bookings")
@login_required
def my_bookings():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT b.booking_id, b.booking_date, b.created_at, b.tickets, b.is_student,
               b.discount_applied, COALESCE(b.status, 'Confirmed') AS status,
               e.event_id, e.event_name, e.event_date, e.location,
               p.amount, p.payment_method, COALESCE(p.payment_status, 'Pending') AS payment_status,
               p.payment_date
        FROM bookings b
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        WHERE b.user_id = %s
        ORDER BY b.created_at DESC, b.booking_id DESC
        """,
        (g.current_user["user_id"],),
    )
    bookings = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("my_bookings.html", bookings=bookings)


@app.route("/bookings/<int:booking_id>/receipt")
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

    return render_template("booking_receipt.html", booking=booking)


@app.route("/bookings/<int:booking_id>/receipt/download")
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

    booked_at = booking.get("created_at") or booking.get("payment_date")
    lines = [
        "Bristol Community Events Booking Receipt",
        "",
        f"Booking ID: {booking['booking_id']}",
        f"Status: {booking['status']}",
        f"Payment status: {booking.get('payment_status') or 'Pending'}",
        f"Booked at: {booked_at.strftime('%Y-%m-%d %H:%M') if booked_at else '-'}",
        "",
        "User details",
        f"Name: {booking.get('full_name') or '-'}",
        f"Email: {booking.get('email') or '-'}",
        f"Phone: {booking.get('phone') or '-'}",
        "",
        "Event details",
        f"Event: {booking.get('event_name') or '-'}",
        f"Date: {booking['event_date'].strftime('%Y-%m-%d') if booking.get('event_date') else '-'}",
        f"Venue: {booking.get('venue_name') or '-'}",
        f"Location: {booking.get('location') or '-'}",
        f"Tickets: {booking.get('tickets') or 0}",
        f"Amount paid: GBP {to_money(booking.get('amount')):.2f}",
    ]
    content = "\n".join(lines)

    return Response(
        content,
        mimetype="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=booking-{booking_id}-receipt.txt"
        },
    )


@app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
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
        return redirect(url_for("my_bookings"))

    try:
        cursor.execute(
            "UPDATE bookings SET status=%s WHERE booking_id=%s",
            ("Cancelled", booking_id),
        )
        cursor.execute(
            """
            UPDATE payments
            SET payment_status=%s, payment_date=%s
            WHERE booking_id=%s
            """,
            ("Cancelled", datetime.now(), booking_id),
        )
        conn.commit()
        flash("Booking cancelled successfully.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Database error: {err}", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("my_bookings"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    next_url = get_safe_next_url() or url_for("admin_dashboard")
    return redirect(url_for("login", next=next_url))


@app.route("/admin/logout")
def admin_logout():
    return redirect(url_for("logout"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS count FROM events")
    events_count = cursor.fetchone()["count"]

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

    cursor.execute(
        """
        SELECT b.booking_id, b.booking_date, b.created_at, b.tickets,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_name, e.event_date,
               COALESCE(p.payment_status, 'Pending') AS payment_status
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        ORDER BY b.created_at DESC, b.booking_id DESC
        LIMIT 5
        """
    )
    recent_bookings = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "admin/dashboard.html",
        events_count=events_count,
        users_count=users_count,
        bookings_count=bookings_count,
        tickets_total=tickets_total,
        revenue_total=revenue_total,
        recent_bookings=recent_bookings,
    )


@app.route("/admin/bookings")
@admin_required
def admin_bookings():
    q = request.args.get("q", "").strip()
    event_id_raw = request.args.get("event_id", "").strip()
    status_raw = request.args.get("status", "").strip()
    date_from_raw = request.args.get("date_from", "").strip()
    date_to_raw = request.args.get("date_to", "").strip()

    date_from = parse_event_date(date_from_raw)
    date_to = parse_event_date(date_to_raw)

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
        filters.append("b.booking_date >= %s")
        params.append(date_from)

    if date_to is None and date_to_raw:
        flash("Invalid end date filter.", "error")
    elif date_to is not None:
        filters.append("b.booking_date <= %s")
        params.append(date_to)

    where_clause = " AND ".join(filters) if filters else "1=1"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        f"""
        SELECT b.booking_id, b.booking_date, b.created_at, b.tickets, b.is_student, b.discount_applied,
               COALESCE(b.status, 'Confirmed') AS status,
               u.full_name, u.email,
               e.event_id, e.event_name, e.event_date,
               p.amount, p.payment_method, COALESCE(p.payment_status, 'Pending') AS payment_status
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        LEFT JOIN payments p ON b.booking_id = p.booking_id
        WHERE {where_clause}
        ORDER BY b.created_at DESC, b.booking_id DESC
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


@app.route("/admin/bookings/<int:booking_id>/edit", methods=["GET", "POST"])
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
                    (total_amount, payment_status, datetime.now(), booking_id),
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
                        datetime.now(),
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


@app.route("/admin/bookings/<int:booking_id>/delete", methods=["POST"])
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


@app.route("/admin/users")
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


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/login", methods=["GET", "POST"])
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


@app.route("/register", methods=["GET", "POST"])
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
            password_hash = generate_password_hash(password)
            if existing:
                if existing.get("password_hash"):
                    flash("An account with that email already exists.", "error")
                    return redirect(url_for("register"))

                cursor.execute(
                    """
                    UPDATE users
                    SET full_name=%s, phone=%s, password_hash=%s, role=%s
                    WHERE user_id=%s
                    """,
                    (
                        full_name,
                        phone,
                        password_hash,
                        existing.get("role") or ROLE_USER,
                        existing["user_id"],
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO users (full_name, email, phone, password_hash, role)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (full_name, email, phone, password_hash, ROLE_USER),
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


@app.route("/logout")
def logout():
    if session.get("user_id"):
        session.clear()
        flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route("/edit_booking/<int:booking_id>", methods=["GET", "POST"])
@login_required
def edit_booking(booking_id):
    if g.current_user.get("role") != ROLE_ADMIN:
        abort(403)
    return admin_edit_booking(booking_id)


@app.route("/delete_booking/<int:booking_id>", methods=["POST"])
@login_required
def delete_booking(booking_id):
    if g.current_user.get("role") == ROLE_ADMIN:
        return admin_delete_booking(booking_id)
    return cancel_booking(booking_id)


@app.errorhandler(403)
def forbidden(_e):
    return render_template("403.html"), 403


@app.errorhandler(404)
def page_not_found(_e):
    return render_template("404.html"), 404


@app.errorhandler(401)
def unauthorized(_e):
    return render_template("401.html"), 401


@app.errorhandler(500)
def internal_error(_e):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
