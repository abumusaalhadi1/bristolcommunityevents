Bristol Community Events - Progress Review 3
===========================================

STUDENT INFORMATION:
-------------------
Name: Md Abu Musa Al Hadi
UWE Student ID: 25060302

PROJECT OVERVIEW:
----------------
The original static Bristol Community Events website has been converted into a dynamic Flask web application
with MySQL integration. The app demonstrates server-side routing, template rendering with Jinja2, and
form submissions stored in a database.

KEY FEATURES:
------------
- Flask routing for all pages (home, events, booking, contact, login)
- Jinja2 template inheritance (base layout + page templates)
- Static assets served through Flask (CSS + JavaScript)
- Dynamic event data loaded from MySQL
- Booking and contact forms stored in MySQL

PROJECT STRUCTURE:
-----------------
app.py
templates/
  base.html
  index.html
  events.html
  booking.html
  contact.html
  login.html
static/
  css/style.css
  js/main.js
  js/login.js
database/
  init.sql

SETUP INSTRUCTIONS:
------------------
1. Create and activate a Python virtual environment (optional but recommended).
2. Install dependencies:
   - pip install flask mysql-connector-python
3. Create the MySQL database and tables:
   - If you already have uwe_events_db from MySQL Workbench, keep it.
   - Ensure the following tables exist: users, events, bookings, contact_messages, categories, venues.
   - Use database/init.sql only if you want to create a fresh schema.
4. Configure environment variables if needed:
   - DB_HOST (default: localhost)
   - DB_USER (default: root)
   - DB_PASSWORD (default: empty)
   - DB_NAME (default: uwe_events_db)
   - DB_PORT (default: 3306)
5. Run the Flask app:
   - python app.py
6. Open the site in your browser:
   - http://127.0.0.1:5000/

NOTES:
-----
- If the MySQL connection is not available, the app will fall back to sample event data so the site still loads.
- Update app.py or the environment variables to match your local MySQL credentials.

PROGRESS REVIEW 3 CHECKLIST:
---------------------------
Week 21
- All pages load through Flask routing.
- Static resources are served via Flask.
- Dynamic event data is loaded from MySQL into the events page.

Week 22
- Booking form posts to Flask and inserts into MySQL.
- Contact form posts to Flask and inserts into MySQL.
- Server responds with success/error feedback.
