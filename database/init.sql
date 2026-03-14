CREATE DATABASE IF NOT EXISTS uwe_events_db;
USE uwe_events_db;

CREATE TABLE IF NOT EXISTS events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_name VARCHAR(255) NOT NULL,
    event_date DATE NOT NULL,
    event_location VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    category VARCHAR(50),
    image_url TEXT,
    tickets_remaining INT,
    last_booking_date DATE,
    conditions VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    event_id INT NOT NULL,
    tickets INT NOT NULL,
    is_student BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO events (event_name, event_date, event_location, description, price, category, image_url, tickets_remaining, last_booking_date, conditions)
VALUES
('Bristol Music Festival', '2024-03-15', 'Ashton Gate Stadium', 'Three days of live music and food stalls.', 45.00, 'music', 'https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80', 42, '2024-03-10', 'Age 18+'),
('Summer Jazz Night', '2024-06-25', 'Bristol Hippodrome', 'An evening of classic and modern jazz.', 35.00, 'music', 'https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80', 15, '2024-06-20', 'Formal dress'),
('Bristol City vs Rovers', '2024-03-20', 'Ashton Gate Stadium', 'Local derby football match.', 25.00, 'sports', 'https://images.unsplash.com/photo-1575361204480-aadea25e6e68?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80', 8, '2024-03-18', 'No alcohol'),
('Bristol City Marathon', '2024-04-05', 'Ashton Gate Stadium', 'City-wide marathon and fun run.', 30.00, 'sports', 'https://images.unsplash.com/photo-1461896836934-ffe607ba8211?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80', 120, '2024-03-25', 'Medical certificate required'),
('Modern Art Exhibition', '2024-04-01', 'Royal West Academy', 'Contemporary art installations and tours.', 0.00, 'exhibitions', 'https://images.unsplash.com/photo-1563089145-599997674d42?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80', NULL, '2024-04-30', 'Free entry'),
('Photography Expo', '2024-05-10', 'Arnolfini', 'Explore Bristol photographers and workshops.', 12.00, 'exhibitions', 'https://images.unsplash.com/photo-1513475382585-d06e58bcb0e0?ixlib=rb-4.0.3&auto=format&fit=crop&w=1350&q=80', 75, '2024-05-08', 'No flash photography');
