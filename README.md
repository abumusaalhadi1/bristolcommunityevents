# Bristol Community Events (Flask)

## Project Overview
A Flask + MySQL web app for browsing Bristol community events, booking tickets, and managing bookings. The UI is fully integrated with Jinja templates while preserving the original styling.

## Technologies Used
- Python (Flask)
- MySQL (mysql-connector-python)
- HTML, CSS, JavaScript (custom UI)

## Features
- Home page with live stats, featured events, and testimonials
- Events listing with filters (category/date/price)
- Event detail pages with booking CTA
- Booking flow with student discount and payment record
- Bookings list with edit + delete actions
- Login/Register (demo logic)
- Custom 404/500 pages

## Database Schema (Core Tables)
- `events`
- `venues`
- `categories`
- `users`
- `bookings`
- `payments`

## How To Run
1. Create the database:
   - `uwe_events_db`
2. Create tables using your SQL schema (events/venues/categories/users/bookings/payments).
3. Install dependencies:
   - `pip install -r my_flask_app/requirements.txt`
4. Run the app:
   - `run_app.bat`
   - or `cd my_flask_app` then `python app.py`
5. Open:
   - `http://127.0.0.1:5001`

## Configuration
Database credentials and secret key live in `my_flask_app/config.py`.
You can override them with environment variables:
- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `SECRET_KEY`
