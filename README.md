# Bristol Community Events (Flask)

A Flask and MySQL web app for browsing Bristol events, booking tickets, joining waitlists, sending contact messages, and managing admin tasks.

## Project Layout
- `my_flask_app/app.py` - main Flask app, routes, auth, and database setup
- `my_flask_app/dbfunc.py` - database connection helper
- `my_flask_app/config.py` - environment-based configuration
- `my_flask_app/receipt.py` - booking receipt PDF helpers
- `my_flask_app/seed_data.py` - sample venues and reviews
- `my_flask_app/blueprints/` - route registration grouped by feature area
- `my_flask_app/templates/` - Jinja templates
- `my_flask_app/static/css/style.css` - site styling
- `my_flask_app/static/js/app.js` - client-side booking logic
- `my_flask_app/db_schema.sql` - schema for a fresh database

## Features
- Home page with featured events, stats, and reviews
- Event browsing with category, date, and price filters
- Event detail pages with ticket booking
- Booking flow with booking-day selection, student and advance-booking discounts, payment details, and PDF receipt
- Waitlist support for sold-out events, including an account flow to accept an offered booking
- My bookings page with receipt access, booking edits, cancellations, and refund checks
- Refund policy page and cancel-booking flow with a 3-day refund window
- Contact form and account pages for signed-in users
- Admin pages for events, venues, bookings, reviews, receipts, users, subscribers, and reports
- Admin tools for editing bookings, reducing event prices, and resetting user passwords
- Blueprint-based route registration for public, account, auth, admin, and error handling
- Custom error pages

## Run It
1. Create the database used in `my_flask_app/config.py` (default: `bristol_community_events_db`).
2. Import `my_flask_app/db_schema.sql` into MySQL.
3. Install dependencies:
   - `pip install -r my_flask_app/requirements.txt`
4. Start the app:
   - `run_app.bat`
   - or `cd my_flask_app` and run `python app.py`
5. Open:
   - `http://127.0.0.1:5001`

## Config
Environment variables used by the app:
- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `SECRET_KEY`
