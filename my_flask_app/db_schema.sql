-- Bristol Events DB Schema
CREATE DATABASE IF NOT EXISTS `bristol_community_events_db`;
USE `bristol_community_events_db`;

CREATE TABLE venues (
  venue_id INT PRIMARY KEY AUTO_INCREMENT,
  venue_name VARCHAR(150) NOT NULL,
  address VARCHAR(255),
  city VARCHAR(100),
  suitable_for VARCHAR(255),
  image_url VARCHAR(500),
  capacity INT NOT NULL
);

CREATE TABLE categories (
  category_id INT PRIMARY KEY AUTO_INCREMENT,
  category_name VARCHAR(100) NOT NULL
);

CREATE TABLE events (
  event_id INT PRIMARY KEY AUTO_INCREMENT,
  event_name VARCHAR(255) NOT NULL,
  description TEXT,
  event_date DATE,
  price DECIMAL(10,2),
  event_cost DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  venue_id INT,
  category_id INT,
  event_capacity INT,
  image_url VARCHAR(255),
  is_featured TINYINT(1) NOT NULL DEFAULT 0,
  featured_order INT NULL,
  FOREIGN KEY (venue_id) REFERENCES venues(venue_id) ON DELETE SET NULL,
  FOREIGN KEY (category_id) REFERENCES categories(category_id)
);

CREATE TABLE users (
  user_id INT PRIMARY KEY AUTO_INCREMENT,
  full_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255),
  password_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  role VARCHAR(20) NOT NULL DEFAULT 'user',
  phone VARCHAR(20),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
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
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  FOREIGN KEY (invited_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE newsletter_subscribers (
  subscriber_id INT PRIMARY KEY AUTO_INCREMENT,
  email VARCHAR(255) UNIQUE NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bookings (
  booking_id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  event_id INT NOT NULL,
  contact_phone VARCHAR(50),
  booking_date DATE,
  tickets INT NOT NULL,
  is_student BOOLEAN DEFAULT FALSE,
  discount_applied DECIMAL(10,2) DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'Confirmed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(user_id),
  FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE TABLE payments (
  payment_id INT PRIMARY KEY AUTO_INCREMENT,
  booking_id INT NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  payment_method VARCHAR(50),
  payment_source VARCHAR(255),
  payment_status VARCHAR(20) NOT NULL,
  payment_date DATETIME,
  FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);

CREATE TABLE contact_messages (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reviews (
  review_id INT PRIMARY KEY AUTO_INCREMENT,
  seed_key VARCHAR(64),
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
);

-- Sample data
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
