Bristol Community Events - Progress Review 3
===========================================

Student Information
-------------------
Name: Md Abu Musa Al Hadi
UWE Student ID: 25060302

Project Overview
----------------
This project converts a static Bristol events website into a fully dynamic Flask web application integrated with a MySQL relational database.

The system allows users to browse events, book tickets, manage their accounts, and interact with the platform, while administrators can manage events, users, bookings, and content through a dedicated dashboard.

The application follows a structured Flask architecture using:
- Python for backend logic
- Jinja2 templates for dynamic HTML rendering
- MySQL for persistent data storage
- Static CSS and JavaScript for frontend design and interactivity

Key Features
------------

Core Website Features
- Dynamic home page displaying:
  - Featured events
  - Event statistics
  - User reviews with ratings
- Event browsing with:
  - Search functionality
  - Category filtering
  - Date and price filtering
  - Event detail pages with full event information

User Authentication & Accounts
- User registration with validation and duplicate email checking
- Secure login system using hashed passwords
- Session-based authentication
- User account management:
  - Update personal details
  - Change password
  - View submitted reviews
  - View contact messages and admin replies

Booking System
- Ticket booking for events
- Validation of:
  - Ticket limits
  - Event availability
  - Duplicate bookings
- Automatic seat availability tracking
- Student discount (10%) applied dynamically
- Booking stored in the database with status tracking

Payment & Receipts
- Payment method selection (card, PayPal, bank)
- Payment details page for the selected method with validation
- Payment records stored in the database
- Booking confirmation system
- Receipt generation:
  - HTML receipt page
  - Downloadable PDF receipt

Refunds
- Refund policy page showing the 3-day refund window
- Cancel booking flow with refund approval or refusal based on event date
- Refunds are processed back to the original payment source

Reviews System
- Logged-in users can submit reviews
- Rating system (1-5 stars)
- Reviews stored with pending status
- Admin moderation (approve, reject, delete reviews)
- Only approved reviews displayed publicly

Contact System
- Logged-in users can submit contact messages
- Messages stored in the database
- Admins can reply to messages
- Users can view admin replies in their account

Admin Panel
- Secure admin-only access using role-based control
- Admin dashboard showing:
  - Total users, bookings, events, revenue
  - Recent activity (bookings, messages, reviews)
- Full CRUD operations for:
  - Events
  - Venues
  - Bookings
  - Users
  - Reviews
  - Contact messages
- Booking management including status updates and recalculation of totals

Technical Features
------------------
- Flask routing for all pages
- Blueprint-based route organisation
- Jinja template inheritance (base/layout structure)
- Static files served via Flask (`url_for`)
- MySQL relational database with foreign key relationships
- Server-side validation and business logic
- Flash messages for user feedback
- Custom error handling (401, 403, 404, 500)
- JavaScript-enhanced UI for booking and validation

Project Structure
-----------------
my_flask_app/
- app.py - Main Flask application (routes and logic)
- dbfunc.py - Database connection helper
- config.py - Configuration and environment variables
- receipt.py - PDF receipt generation
- seed_data.py - Sample data for venues and reviews
- db_schema.sql - Database schema
- blueprints/ - Modular route organisation
- templates/ - Jinja HTML templates
- static/
  - css/style.css - Styling
  - js/app.js - Frontend interactivity

Setup Instructions
------------------
1. Optional: create a virtual environment.
2. Install dependencies:
   - `pip install -r my_flask_app/requirements.txt`
3. Set up the database:
   - Create database: `bristol_community_events_db`
   - Import: `my_flask_app/db_schema.sql`
4. Configure environment variables (optional):
   - `DB_HOST` (default: `localhost`)
   - `DB_USER` (default: `root`)
   - `DB_PASSWORD`
   - `DB_NAME` (default: `bristol_community_events_db`)
   - `DB_PORT` (default: `3306`)
5. Run the application:
   - `run_app.bat`
   - or:
     - `cd my_flask_app`
     - `python app.py`
6. Open in browser:
   - `http://127.0.0.1:5001/`

Notes
-----
- The application automatically seeds sample data (venues and reviews) if tables are empty.
- Database configuration can be changed in `config.py` or via environment variables.
- Passwords are securely hashed before storing.
- The system uses role-based access control for admin features.

Progress Review 3 Checklist
---------------------------

Week 21
- All pages load through Flask routing
- CSS, JavaScript, and images served via Flask
- Dynamic data retrieved from MySQL and displayed in templates

Week 22
- HTML forms send data to Flask (POST requests)
- Flask processes and validates input
- Data is stored in MySQL (bookings, users, messages, reviews)
- Business logic applied (discounts, validation, availability checks)
- Appropriate responses returned (flash messages, redirects, receipts)
