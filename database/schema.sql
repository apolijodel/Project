-- Pasig City LGU - Demand Aggregation & Supply Replenishment System
-- MySQL / MariaDB schema (works with XAMPP).
-- Import in phpMyAdmin, or run: python setup_db.py

CREATE DATABASE IF NOT EXISTS pasig_dasrs_db
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE pasig_dasrs_db;

CREATE TABLE IF NOT EXISTS barangays (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_barangays_name (name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS users (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    username       VARCHAR(60) NOT NULL,
    password_hash  VARCHAR(255) NOT NULL,
    role           ENUM('city', 'barangay') NOT NULL,
    barangay_id    INT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at  DATETIME NULL,
    notifications_seen_at DATETIME NULL,
    UNIQUE KEY uq_users_username (username),
    CONSTRAINT fk_users_barangay FOREIGN KEY (barangay_id)
        REFERENCES barangays (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS medicines (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(150) NOT NULL,
    stock       INT NOT NULL DEFAULT 0,
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_medicines_name (name),
    CONSTRAINT chk_medicines_stock CHECK (stock >= 0)
) ENGINE=InnoDB;

-- Medicines a barangay has received, and how much it currently holds.
CREATE TABLE IF NOT EXISTS barangay_inventory (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    barangay_id  INT NOT NULL,
    medicine_id  INT NOT NULL,
    stock        INT NOT NULL DEFAULT 0,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_inventory_barangay_medicine (barangay_id, medicine_id),
    CONSTRAINT fk_inventory_barangay FOREIGN KEY (barangay_id)
        REFERENCES barangays (id) ON DELETE CASCADE,
    CONSTRAINT fk_inventory_medicine FOREIGN KEY (medicine_id)
        REFERENCES medicines (id) ON DELETE CASCADE,
    CONSTRAINT chk_inventory_stock CHECK (stock >= 0)
) ENGINE=InnoDB;

-- Supply requests (demand signal). Workflow: pending -> approved -> released,
-- or pending/approved -> rejected, or pending -> cancelled (by the barangay).
-- source = 'direct' marks distributions initiated by the city.
CREATE TABLE IF NOT EXISTS requests (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    barangay_id   INT NOT NULL,
    medicine_id   INT NOT NULL,
    quantity      INT NOT NULL,
    status        ENUM('pending', 'approved', 'released', 'rejected', 'cancelled')
                  NOT NULL DEFAULT 'pending',
    source        ENUM('request', 'direct') NOT NULL DEFAULT 'request',
    requested_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action_by     VARCHAR(60) NULL,
    action_at     DATETIME NULL,
    remarks       VARCHAR(255) NULL,
    KEY idx_requests_status (status, requested_at),
    KEY idx_requests_barangay (barangay_id, requested_at),
    KEY idx_requests_medicine (medicine_id, requested_at),
    CONSTRAINT fk_requests_barangay FOREIGN KEY (barangay_id)
        REFERENCES barangays (id) ON DELETE CASCADE,
    CONSTRAINT fk_requests_medicine FOREIGN KEY (medicine_id)
        REFERENCES medicines (id) ON DELETE CASCADE,
    CONSTRAINT chk_requests_quantity CHECK (quantity > 0)
) ENGINE=InnoDB;

-- Every status change of a request (who did what, when) - drives timelines and notifications.
CREATE TABLE IF NOT EXISTS request_events (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    request_id  INT NOT NULL,
    status      ENUM('pending', 'approved', 'released', 'rejected', 'cancelled') NOT NULL,
    actor       VARCHAR(60) NULL,
    note        VARCHAR(255) NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_events_request (request_id, created_at),
    KEY idx_events_created (created_at),
    CONSTRAINT fk_events_request FOREIGN KEY (request_id)
        REFERENCES requests (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Audit trail of every stock change.
--   restock:  city stock increased
--   release:  city stock moved to a barangay
--   dispense: barangay stock used / given out
CREATE TABLE IF NOT EXISTS stock_movements (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    medicine_id   INT NOT NULL,
    barangay_id   INT NULL,
    request_id    INT NULL,
    movement      ENUM('restock', 'release', 'dispense') NOT NULL,
    quantity      INT NOT NULL,
    performed_by  VARCHAR(60) NULL,
    note          VARCHAR(255) NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_movements_created (created_at),
    KEY idx_movements_barangay (barangay_id, created_at),
    CONSTRAINT fk_movements_medicine FOREIGN KEY (medicine_id)
        REFERENCES medicines (id) ON DELETE CASCADE,
    CONSTRAINT fk_movements_barangay FOREIGN KEY (barangay_id)
        REFERENCES barangays (id) ON DELETE CASCADE,
    CONSTRAINT fk_movements_request FOREIGN KEY (request_id)
        REFERENCES requests (id) ON DELETE SET NULL,
    CONSTRAINT chk_movements_quantity CHECK (quantity > 0)
) ENGINE=InnoDB;
