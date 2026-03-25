-- Bristol Events DB Schema
CREATE DATABASE IF NOT EXISTS `bristol_community_events_db`;
USE `bristol_community_events_db`;

CREATE TABLE venues (
  venue_id INT PRIMARY KEY AUTO_INCREMENT,
  venue_name VARCHAR(255) NOT NULL,
  address TEXT
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
  venue_id INT,
  category_id INT,
  event_capacity INT,
  FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
  FOREIGN KEY (category_id) REFERENCES categories(category_id)
);

CREATE TABLE users (
  user_id INT PRIMARY KEY AUTO_INCREMENT,
  full_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255),
  role VARCHAR(20) NOT NULL DEFAULT 'user',
  phone VARCHAR(20),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bookings (
  booking_id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  event_id INT NOT NULL,
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

CREATE TABLE testimonials (
  id INT PRIMARY KEY AUTO_INCREMENT,
  content TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sample data
INSERT INTO venues (venue_name, address) VALUES ('Bristol Cathedral', 'College Green');
