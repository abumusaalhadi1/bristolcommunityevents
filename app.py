import os
from decimal import Decimal

from flask import Flask, render_template, request

try:
    import mysql.connector
    from mysql.connector import Error
except Exception:  # pragma: no cover - handled gracefully at runtime
    mysql = None
    Error = Exception

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret')

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'uwe_events_db'),
    'port': int(os.environ.get('DB_PORT', '3306')),
}

CATEGORY_IMAGES = {
    'music': 'https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
    'sports': 'https://images.unsplash.com/photo-1575361204480-aadea25e6e68?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
    'exhibitions': 'https://images.unsplash.com/photo-1563089145-599997674d42?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
    'workshops': 'https://images.unsplash.com/photo-1513475382585-d06e58bcb0e0?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
    'theatre': 'https://images.unsplash.com/photo-1497032205916-ac775f0649ae?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
}
DEFAULT_EVENT_IMAGE = CATEGORY_IMAGES['music']


SAMPLE_EVENTS = [
    {
        'id': 1,
        'event_name': 'Bristol Music Festival',
        'event_date': 'March 15-17, 2024',
        'event_location': 'Ashton Gate Stadium',
        'description': 'Three days of live music and food stalls.',
        'price': Decimal('45.00'),
        'category': 'music',
        'image_url': 'https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
        'tickets_remaining': 42,
        'last_booking_date': 'March 10, 2024',
        'conditions': 'Age 18+',
    },
    {
        'id': 2,
        'event_name': 'Summer Jazz Night',
        'event_date': 'June 25, 2024',
        'event_location': 'Bristol Hippodrome',
        'description': 'An evening of classic and modern jazz.',
        'price': Decimal('35.00'),
        'category': 'music',
        'image_url': 'https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
        'tickets_remaining': 15,
        'last_booking_date': 'June 20, 2024',
        'conditions': 'Formal dress',
    },
    {
        'id': 3,
        'event_name': 'Bristol City vs Rovers',
        'event_date': 'March 20, 2024',
        'event_location': 'Ashton Gate Stadium',
        'description': 'Local derby football match.',
        'price': Decimal('25.00'),
        'category': 'sports',
        'image_url': 'https://images.unsplash.com/photo-1575361204480-aadea25e6e68?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
        'tickets_remaining': 8,
        'last_booking_date': 'March 18, 2024',
        'conditions': 'No alcohol',
    },
    {
        'id': 4,
        'event_name': 'Bristol City Marathon',
        'event_date': 'April 5, 2024',
        'event_location': 'Ashton Gate Stadium',
        'description': 'City-wide marathon and fun run.',
        'price': Decimal('30.00'),
        'category': 'sports',
        'image_url': 'https://images.unsplash.com/photo-1461896836934-ffe607ba8211?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
        'tickets_remaining': 120,
        'last_booking_date': 'March 25, 2024',
        'conditions': 'Medical certificate required',
    },
    {
        'id': 5,
        'event_name': 'Modern Art Exhibition',
        'event_date': 'April 1-30, 2024',
        'event_location': 'Royal West Academy',
        'description': 'Contemporary art installations and tours.',
        'price': Decimal('0.00'),
        'category': 'exhibitions',
        'image_url': 'https://images.unsplash.com/photo-1563089145-599997674d42?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
        'tickets_remaining': None,
        'last_booking_date': 'April 30, 2024',
        'conditions': 'Free entry',
    },
    {
        'id': 6,
        'event_name': 'Photography Expo',
        'event_date': 'May 10-15, 2024',
        'event_location': 'Arnolfini',
        'description': 'Explore Bristol photographers and workshops.',
        'price': Decimal('12.00'),
        'category': 'exhibitions',
        'image_url': 'https://images.unsplash.com/photo-1513475382585-d06e58bcb0e0?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80',
        'tickets_remaining': 75,
        'last_booking_date': 'May 8, 2024',
        'conditions': 'No flash photography',
    },
]


def get_db_connection():
    if mysql is None:
        app.logger.warning('mysql-connector-python is not installed. Using sample data only.')
        return None

    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as exc:
        app.logger.warning('Database connection failed: %s', exc)
        return None


def format_price(value):
    if value is None:
        return 'Free'

    try:
        numeric = Decimal(str(value))
    except Exception:
        return str(value)

    if numeric == 0:
        return 'Free'

    return f'GBP {numeric:.2f}'


def normalize_events(events):
    normalized = []
    for event in events:
        event_date = event.get('event_date')
        last_booking = event.get('last_booking_date')

        event_date_display = (
            event_date.strftime('%B %d, %Y')
            if hasattr(event_date, 'strftime')
            else str(event_date)
        )
        last_booking_display = (
            last_booking.strftime('%B %d, %Y')
            if hasattr(last_booking, 'strftime')
            else str(last_booking)
        )

        normalized.append({
            **event,
            'event_date_display': event_date_display,
            'last_booking_date_display': last_booking_display,
            'price_display': format_price(event.get('price')),
        })

    return normalized


def fetch_events(category=None, limit=None):
    connection = get_db_connection()
    if connection is None:
        events = SAMPLE_EVENTS
        if category:
            events = [event for event in events if event.get('category') == category]
        if limit:
            events = events[:limit]
        return normalize_events(events)

    query = (
        'SELECT e.event_id, e.event_name, e.event_date, e.location, e.price, e.event_capacity, '
        'c.category_name, v.venue_name '
        'FROM events e '
        'LEFT JOIN categories c ON e.category_id = c.category_id '
        'LEFT JOIN venues v ON e.venue_id = v.venue_id'
    )
    params = []
    if category:
        query += " WHERE LOWER(c.category_name) LIKE CONCAT('%', LOWER(%s), '%')"
        params.append(category)
    query += ' ORDER BY e.event_date ASC'
    if limit:
        query += ' LIMIT %s'
        params.append(limit)

    cursor = connection.cursor(dictionary=True)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    connection.close()

    events = []
    for row in rows:
        category_name = row.get('category_name') or 'General'
        category_slug = category_name.lower().replace(' ', '-')
        events.append({
            'id': row.get('event_id'),
            'event_name': row.get('event_name'),
            'event_date': row.get('event_date'),
            'event_location': row.get('location') or row.get('venue_name') or 'Bristol',
            'description': f'{category_name} event in Bristol.',
            'price': row.get('price'),
            'category': category_slug,
            'image_url': CATEGORY_IMAGES.get(category_slug, DEFAULT_EVENT_IMAGE),
            'tickets_remaining': row.get('event_capacity'),
            'last_booking_date': None,
            'conditions': 'Standard entry',
        })

    return normalize_events(events)


def create_booking(data):
    connection = get_db_connection()
    if connection is None:
        return False

    user_id = get_or_create_user(connection, data['name'], data['email'], data.get('phone'))
    price = fetch_event_price(connection, data['event_id'])
    discount = Decimal('0.00')
    if data['is_student'] and price is not None:
        discount = (Decimal(str(price)) * Decimal('0.10')).quantize(Decimal('0.01'))

    cursor = connection.cursor()
    cursor.execute(
        (
            'INSERT INTO bookings (user_id, event_id, booking_date, tickets, is_student, discount_applied) '
            'VALUES (%s, %s, CURDATE(), %s, %s, %s)'
        ),
        (
            user_id,
            data['event_id'],
            data['tickets'],
            1 if data['is_student'] else 0,
            discount,
        ),
    )
    connection.commit()
    cursor.close()
    connection.close()
    return True


def get_or_create_user(connection, name, email, phone):
    cursor = connection.cursor()
    cursor.execute('SELECT user_id FROM users WHERE email = %s', (email,))
    row = cursor.fetchone()
    if row:
        user_id = row[0]
    else:
        cursor.execute(
            'INSERT INTO users (full_name, email, phone) VALUES (%s, %s, %s)',
            (name, email, phone),
        )
        connection.commit()
        user_id = cursor.lastrowid
    cursor.close()
    return user_id


def fetch_event_price(connection, event_id):
    cursor = connection.cursor()
    cursor.execute('SELECT price FROM events WHERE event_id = %s', (event_id,))
    row = cursor.fetchone()
    cursor.close()
    if not row:
        return None
    return row[0]


def create_contact_message(data):
    connection = get_db_connection()
    if connection is None:
        return False

    cursor = connection.cursor()
    cursor.execute(
        (
            'INSERT INTO contact_messages (name, email, subject, message) '
            'VALUES (%s, %s, %s, %s)'
        ),
        (data['name'], data['email'], data['subject'], data['message']),
    )
    connection.commit()
    cursor.close()
    connection.close()
    return True


@app.route('/')
def home():
    featured_events = fetch_events(limit=3)
    return render_template('index.html', featured_events=featured_events)


@app.route('/events')
def events():
    category = request.args.get('category')
    all_events = fetch_events(category=category)
    return render_template('events.html', events=all_events, selected_category=category)


@app.route('/booking')
def booking():
    events_list = fetch_events()
    success = request.args.get('success') == '1'
    return render_template('booking.html', events=events_list, success=success, errors=[], form_data={})


@app.route('/book', methods=['POST'])
def book():
    events_list = fetch_events()
    form_data = {
        'event_id': request.form.get('event_id', '').strip(),
        'tickets': request.form.get('tickets', '').strip(),
        'name': request.form.get('name', '').strip(),
        'email': request.form.get('email', '').strip(),
        'phone': request.form.get('phone', '').strip(),
        'student': request.form.get('student') == 'on',
    }

    errors = []
    if not form_data['event_id']:
        errors.append('Please select an event.')
    if not form_data['tickets'] or not form_data['tickets'].isdigit():
        errors.append('Please enter a valid number of tickets.')
    else:
        ticket_count = int(form_data['tickets'])
        if ticket_count < 1 or ticket_count > 10:
            errors.append('Tickets must be between 1 and 10.')
    if not form_data['name']:
        errors.append('Please enter your full name.')
    if not form_data['email']:
        errors.append('Please enter a valid email address.')

    if errors:
        return render_template('booking.html', events=events_list, success=False, errors=errors, form_data=form_data)

    booking_data = {
        'event_id': int(form_data['event_id']),
        'tickets': int(form_data['tickets']),
        'name': form_data['name'],
        'email': form_data['email'],
        'phone': form_data['phone'] or None,
        'is_student': form_data['student'],
    }

    saved = create_booking(booking_data)
    if not saved:
        errors.append('Database connection is unavailable. Please try again later.')
        return render_template('booking.html', events=events_list, success=False, errors=errors, form_data=form_data)

    return render_template('booking.html', events=events_list, success=True, errors=[], form_data={})


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    success = False
    errors = []
    form_data = {
        'name': '',
        'email': '',
        'subject': '',
        'message': '',
    }

    if request.method == 'POST':
        form_data = {
            'name': request.form.get('name', '').strip(),
            'email': request.form.get('email', '').strip(),
            'subject': request.form.get('subject', '').strip(),
            'message': request.form.get('message', '').strip(),
        }

        if not form_data['name']:
            errors.append('Please enter your name.')
        if not form_data['email']:
            errors.append('Please enter your email.')
        if not form_data['subject']:
            errors.append('Please enter a subject.')
        if not form_data['message']:
            errors.append('Please enter a message.')

        if not errors:
            saved = create_contact_message(form_data)
            if saved:
                success = True
                form_data = {'name': '', 'email': '', 'subject': '', 'message': ''}
            else:
                errors.append('Database connection is unavailable. Please try again later.')

    return render_template('contact.html', success=success, errors=errors, form_data=form_data)


@app.route('/login')
def login():
    return render_template('login.html')


if __name__ == '__main__':
    app.run(debug=True)


