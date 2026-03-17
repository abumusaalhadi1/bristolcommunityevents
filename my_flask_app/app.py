from datetime import datetime

import mysql.connector
from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = "your-secret-key-here"

# Database configuration
# Update these values for your local MySQL instance.
db_config = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "Amah#@#98UK",
    "database": "uwe_events_db",
}


def get_db_connection():
    return mysql.connector.connect(**db_config)


@app.route("/")
def home():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Stats
    cursor.execute("SELECT COUNT(*) as count FROM events")
    events_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(DISTINCT venue_id) as count FROM venues")
    venues_count = cursor.fetchone()["count"]

    cursor.execute("SELECT SUM(tickets) as total FROM bookings")
    total_tickets = cursor.fetchone()["total"] or 0

    # Featured events (upcoming 3 events)
    cursor.execute(
        """
        SELECT e.*, v.venue_name, c.category_name
        FROM events e
        JOIN venues v ON e.venue_id = v.venue_id
        JOIN categories c ON e.category_id = c.category_id
        WHERE e.event_date >= CURDATE()
        ORDER BY e.event_date
        LIMIT 3
        """
    )
    featured_events = cursor.fetchall()

    # Testimonials (if you have a testimonials table)
    try:
        cursor.execute("SELECT * FROM testimonials ORDER BY created_at DESC LIMIT 4")
        testimonials = cursor.fetchall()
    except mysql.connector.Error:
        testimonials = []

    cursor.close()
    conn.close()

    return render_template(
        "index.html",
        events_count=events_count,
        venues_count=venues_count,
        attendees_count=total_tickets,
        featured_events=featured_events,
        testimonials=testimonials,
    )


@app.route("/events")
def events():
    category = request.args.get("category")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if category:
        cursor.execute(
            """
            SELECT e.*, v.venue_name, c.category_name
            FROM events e
            JOIN venues v ON e.venue_id = v.venue_id
            JOIN categories c ON e.category_id = c.category_id
            WHERE c.category_name = %s AND e.event_date >= CURDATE()
            ORDER BY e.event_date
            """,
            (category,),
        )
    else:
        cursor.execute(
            """
            SELECT e.*, v.venue_name, c.category_name
            FROM events e
            JOIN venues v ON e.venue_id = v.venue_id
            JOIN categories c ON e.category_id = c.category_id
            WHERE e.event_date >= CURDATE()
            ORDER BY e.event_date
            """
        )

    events_rows = cursor.fetchall()

    cursor.execute("SELECT * FROM categories")
    categories = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template("events.html", events=events_rows, categories=categories)


@app.route("/event/<int:event_id>")
def event_detail(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT e.*, v.venue_name, v.address, c.category_name
        FROM events e
        JOIN venues v ON e.venue_id = v.venue_id
        JOIN categories c ON e.category_id = c.category_id
        WHERE e.event_id = %s
        """,
        (event_id,),
    )
    event = cursor.fetchone()
    cursor.close()
    conn.close()

    if not event:
        return render_template("404.html"), 404

    return render_template("event_details.html", event=event)


@app.route("/book/<int:event_id>", methods=["GET", "POST"])
def book(event_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT e.*, v.venue_name
        FROM events e
        JOIN venues v ON e.venue_id = v.venue_id
        WHERE e.event_id = %s
        """,
        (event_id,),
    )
    event = cursor.fetchone()
    if not event:
        cursor.close()
        conn.close()
        return render_template("404.html"), 404

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        tickets = int(request.form["tickets"])
        is_student = "is_student" in request.form

        if not full_name or not email or tickets < 1:
            flash("Please fill in all required fields.", "error")
            cursor.close()
            conn.close()
            return redirect(url_for("book", event_id=event_id))

        price = event["price"] or 0
        discount = 0
        if is_student:
            discount = price * tickets * 0.10
        total_amount = (price * tickets) - discount

        try:
            cursor.execute(
                """
                INSERT INTO users (full_name, email, phone)
                VALUES (%s, %s, %s)
                """,
                (full_name, email, phone),
            )
            user_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO bookings (user_id, event_id, booking_date, tickets, is_student, discount_applied)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, event_id, datetime.now().date(), tickets, is_student, discount),
            )
            booking_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO payments (booking_id, amount, payment_method, payment_status, payment_date)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (booking_id, total_amount, "card", "PAID", datetime.now()),
            )

            conn.commit()
            flash("Booking successful! Check your email for confirmation.", "success")
        except mysql.connector.Error as err:
            conn.rollback()
            flash(f"Database error: {err}", "error")
        finally:
            cursor.close()
            conn.close()

        return redirect(url_for("event_detail", event_id=event_id))

    cursor.close()
    conn.close()
    return render_template("booking.html", event=event)


@app.route("/bookings")
def bookings_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT b.*, u.full_name, u.email, e.event_name, e.event_date
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        ORDER BY b.booking_date DESC
        """
    )
    bookings = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("bookings.html", bookings=bookings)


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Please enter your email and password.", "error")
            return redirect(url_for("login"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user:
            flash("Login successful (demo).", "success")
            return redirect(url_for("home"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not full_name or not email or not password:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        existing = cursor.fetchone()

        if existing:
            cursor.close()
            conn.close()
            flash("An account with that email already exists.", "error")
            return redirect(url_for("register"))

        cursor.execute(
            """
            INSERT INTO users (full_name, email, phone)
            VALUES (%s, %s, %s)
            """,
            (full_name, email, ""),
        )
        conn.commit()
        cursor.close()
        conn.close()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/edit_booking/<int:booking_id>", methods=["GET", "POST"])
def edit_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        tickets = int(request.form["tickets"])
        is_student = "is_student" in request.form

        cursor.execute(
            """
            SELECT e.price FROM bookings b
            JOIN events e ON b.event_id = e.event_id
            WHERE b.booking_id = %s
            """,
            (booking_id,),
        )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return render_template("404.html"), 404

        price = row["price"]
        discount = 0
        if is_student:
            discount = price * tickets * 0.10
        total_amount = (price * tickets) - discount

        cursor.execute(
            """
            UPDATE bookings
            SET tickets=%s, is_student=%s, discount_applied=%s
            WHERE booking_id=%s
            """,
            (tickets, is_student, discount, booking_id),
        )

        cursor.execute(
            """
            UPDATE payments SET amount=%s WHERE booking_id=%s
            """,
            (total_amount, booking_id),
        )

        conn.commit()
        cursor.close()
        conn.close()
        flash("Booking updated!", "success")
        return redirect(url_for("bookings_list"))

    cursor.execute(
        """
        SELECT b.*, u.full_name, u.email, e.event_name, e.event_date, e.price
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        JOIN events e ON b.event_id = e.event_id
        WHERE b.booking_id = %s
        """,
        (booking_id,),
    )
    booking = cursor.fetchone()
    cursor.close()
    conn.close()

    if not booking:
        return render_template("404.html"), 404

    return render_template("edit_booking.html", booking=booking)


@app.route("/delete_booking/<int:booking_id>", methods=["POST"])
def delete_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM payments WHERE booking_id = %s", (booking_id,))
        cursor.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
        conn.commit()
        flash("Booking cancelled.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err}", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("bookings_list"))


@app.errorhandler(404)
def page_not_found(_e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(_e):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
