Bristol Community Events - Progress Review 3
===========================================

STUDENT INFORMATION:
-------------------
Name: Md Abu Musa Al Hadi
UWE Student ID: 25060302

PROJECT OVERVIEW:
----------------
This project turns a static Bristol events website into a Flask application backed by MySQL.
The code is split into clear files for the app, receipt generation, seed data, database access,
configuration, blueprint-based route registration, templates, and static assets.

KEY FEATURES:
------------
- Flask routing for home, events, booking, contact, login, and admin pages
- Jinja template inheritance with shared layouts
- Static assets served through Flask
- Event, booking, review, and contact data stored in MySQL
- Booking receipts generated as PDF downloads

PROJECT STRUCTURE:
-----------------
my_flask_app/app.py
my_flask_app/receipt.py
my_flask_app/seed_data.py
my_flask_app/dbfunc.py
my_flask_app/config.py
my_flask_app/blueprints/
my_flask_app/db_schema.sql
my_flask_app/templates/
my_flask_app/static/css/style.css
my_flask_app/static/js/app.js

SETUP INSTRUCTIONS:
------------------
1. Create and activate a Python virtual environment if you want to keep the install isolated.
2. Install dependencies:
   - `pip install -r my_flask_app/requirements.txt`
3. Create the MySQL database and tables:
   - The default database name is `bristol_community_events_db`
   - Import `my_flask_app/db_schema.sql` to create a fresh schema
4. Configure environment variables if needed:
   - `DB_HOST` (default: localhost)
   - `DB_USER` (default: root)
   - `DB_PASSWORD`
   - `DB_NAME` (default: bristol_community_events_db)
   - `DB_PORT` (default: 3306)
5. Run the Flask app:
   - `run_app.bat`
   - or `cd my_flask_app` then `python app.py`
6. Open the site in your browser:
   - `http://127.0.0.1:5001/`

NOTES:
-----
- The app seeds sample venues and reviews when the tables are empty.
- Update `my_flask_app/config.py` or the environment variables to match your local MySQL setup.

PROGRESS REVIEW 3 CHECKLIST:
---------------------------
Week 21
- All pages load through Flask routing.
- Static resources are served via Flask.
- Dynamic event data is loaded from MySQL into the events page.

Week 22
- Booking form posts to Flask and inserts into MySQL.
- Contact form posts to Flask and inserts into MySQL.
- Server responds with success and error feedback.
