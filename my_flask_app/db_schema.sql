-- Bristol Events DB Schema
CREATE DATABASE IF NOT EXISTS `bristol_community_events_db`;
USE `bristol_community_events_db`;

CREATE TABLE venues (
  venue_id INT PRIMARY KEY AUTO_INCREMENT,
  venue_name VARCHAR(150) NOT NULL,
  address VARCHAR(255) NULL,
  city VARCHAR(100) NULL,
  suitable_for VARCHAR(255) NULL,
  image_url VARCHAR(500) NULL,
  capacity INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE categories (
  category_id INT PRIMARY KEY AUTO_INCREMENT,
  category_name VARCHAR(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
  user_id INT PRIMARY KEY AUTO_INCREMENT,
  full_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NULL,
  password_changed_at DATETIME NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'user',
  phone VARCHAR(20) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE events (
  event_id INT PRIMARY KEY AUTO_INCREMENT,
  event_name VARCHAR(255) NOT NULL,
  description TEXT NULL,
  location VARCHAR(255) NULL,
  event_date DATE NULL,
  event_end_date DATE NULL,
  event_time VARCHAR(50) NULL,
  conditions TEXT NULL,
  price DECIMAL(10,2) NULL,
  event_cost DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  venue_id INT NULL,
  category_id INT NULL,
  event_capacity INT NULL,
  image_url VARCHAR(255) NULL,
  is_featured TINYINT(1) NOT NULL DEFAULT 0,
  featured_order INT NULL,
  CONSTRAINT fk_events_venue
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id) ON DELETE SET NULL,
  CONSTRAINT fk_events_category
    FOREIGN KEY (category_id) REFERENCES categories(category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE bookings (
  booking_id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  event_id INT NOT NULL,
  waitlist_id INT NULL,
  contact_phone VARCHAR(50) NULL,
  booking_date DATE NULL,
  tickets INT NOT NULL,
  booking_days INT NOT NULL DEFAULT 1,
  subtotal_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  student_discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  advance_discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  discount_applied DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  cancellation_charge DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  refund_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  status VARCHAR(20) NOT NULL DEFAULT 'Confirmed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_bookings_user
    FOREIGN KEY (user_id) REFERENCES users(user_id),
  CONSTRAINT fk_bookings_event
    FOREIGN KEY (event_id) REFERENCES events(event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE payments (
  payment_id INT PRIMARY KEY AUTO_INCREMENT,
  booking_id INT NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  payment_method VARCHAR(50) NULL,
  payment_source VARCHAR(255) NULL,
  payment_status VARCHAR(20) NOT NULL,
  payment_date DATETIME NULL,
  CONSTRAINT fk_payments_booking
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE contact_messages (
  message_id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  sender_name VARCHAR(255) NOT NULL,
  sender_email VARCHAR(255) NOT NULL,
  sender_phone VARCHAR(50) NULL,
  subject VARCHAR(255) NOT NULL,
  message TEXT NOT NULL,
  admin_reply TEXT NULL,
  replied_by INT NULL,
  replied_at DATETIME NULL,
  user_deleted_at DATETIME NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'New',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_contact_messages_user
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  CONSTRAINT fk_contact_messages_replied_by
    FOREIGN KEY (replied_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reviews (
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
  CONSTRAINT fk_reviews_user
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
  CONSTRAINT fk_reviews_reviewed_by
    FOREIGN KEY (reviewed_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE testimonials (
  id INT PRIMARY KEY AUTO_INCREMENT,
  author_name VARCHAR(255) NOT NULL,
  author_initials VARCHAR(10) NOT NULL,
  rating TINYINT UNSIGNED NOT NULL DEFAULT 5,
  content TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE event_waitlist (
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
  CONSTRAINT fk_event_waitlist_event
    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
  CONSTRAINT fk_event_waitlist_user
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE newsletter_subscribers (
  subscriber_id INT PRIMARY KEY AUTO_INCREMENT,
  email VARCHAR(255) UNIQUE NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE password_reset_tokens (
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
  CONSTRAINT fk_password_reset_tokens_user
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  CONSTRAINT fk_password_reset_tokens_requested_by
    FOREIGN KEY (requested_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE role_invitation_tokens (
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
  CONSTRAINT fk_role_invitation_tokens_user
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  CONSTRAINT fk_role_invitation_tokens_invited_by
    FOREIGN KEY (invited_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE schema_migrations (
  migration_key VARCHAR(100) PRIMARY KEY,
  applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO venues (venue_name, address, city, suitable_for, image_url, capacity) VALUES
('Bristol City Centre Hall', 'Broad Street, Bristol BS1 2EA', 'Bristol', 'Conferences, Exhibitions, Community Gatherings', 'images/venue/Bristol City Centre Hall.jpg', 500),
('Harbourside Art Space', 'Dock Street, Bristol BS1 5AQ', 'Bristol', 'Exhibitions, Workshops, Music', 'images/venue/Harbourside Art Space.jpg', 300),
('Ashton Court Estate', 'Ashton Court Estate, Long Ashton, Bristol BS41 9JN', 'Bristol', 'Outdoor Events, Festivals, Exhibitions', 'images/venue/Ashton Court Estate.jpg', 400),
('Bristol Indoor Arena', 'Arena Road, Bristol BS1 5TT', 'Bristol', 'Sports, Concerts, Exhibitions', 'images/venue/Bristol Indoor Arena.jpg', 500),
('Ashton Gate Stadium', 'Ashton Gate Stadium, Ashton Road, Bristol BS3 2EJ', 'Bristol', 'Musical, Sports, Exhibitions', 'images/venue/Ashton Gate Stadium.jpg', 150),
('Arnolfini', '16 Narrow Quay, Bristol BS1 4QA', 'Bristol', 'Exhibitions, Workshops', 'images/venue/Arnolfini.jpg', 100),
('The Bristol Hippodrome', 'St Augustine''s Parade, Bristol BS1 4UZ', 'Bristol', 'Theatre, Musical', 'images/venue/The Bristol Hippodrome.jpg', 120),
('Bristol Old Vic', 'King Street, Bristol BS1 4ED', 'Bristol', 'Theatre', 'images/venue/Bristol Old Vic.jpg', 110),
('Bristol Central Library', 'Deanery Road, City Centre, Bristol BS1 5TL', 'Bristol', 'Library Exhibitions', 'images/venue/Bristol Central Library.jpg', 50),
('Royal West of England Academy', 'Queens Road, Clifton, Bristol BS8 1PX', 'Bristol', 'Exhibitions', 'images/venue/Royal West of England Academy.jpg', 100),
('UWE Exhibition Centre', 'North Entrance, Frenchay Campus, Filton Road, Bristol BS34 8QZ', 'Bristol', 'Wedding, Workshops, Conference, Exhibitions', 'images/venue/UWE Exhibition Centre.jpg', 300),
('Creative Space A', '28 King Street, Bristol BS1 4EF', 'Bristol', 'Workshops', 'images/venue/Creative Space A.jpg', 30),
('Creative Space B', '12 Temple Gate, Bristol BS1 6ED', 'Bristol', 'Workshops, Courses', 'images/venue/Creative Space B.jpg', 50),
('Community Centre A', '101 Church Road, Bristol BS5 8AF', 'Bristol', 'Private events (e.g., birthday parties, religious events)', 'images/venue/Community Centre A.jpg', 60);

INSERT INTO categories (category_name) VALUES
('Arts'),
('Family'),
('Festival'),
('Food & Drink'),
('Music'),
('Outdoor');
