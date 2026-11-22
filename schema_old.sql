-- Grain Store mini project - database schema (4 tables)
-- Run once:  mysql -u root -p < schema.sql
DROP DATABASE IF EXISTS grain_store;
CREATE DATABASE IF NOT EXISTS grain_store;
USE grain_store;

DROP TABLE IF EXISTS bill_items;
DROP TABLE IF EXISTS bills;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS users;

-- 1) who can log in
CREATE TABLE users (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    username  VARCHAR(50)  NOT NULL UNIQUE,
    password  VARCHAR(64)  NOT NULL,                 -- SHA-256 hash, never plain text
    role      ENUM('owner','cashier') NOT NULL
);

-- 2) what the shop sells (price per kg, stock in kg)
CREATE TABLE products (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    name      VARCHAR(100) NOT NULL UNIQUE,
    category  VARCHAR(50)  NOT NULL,                 -- Rice, Wheat, Pulses ...
    price     DECIMAL(10,2) NOT NULL CHECK (price > 0),
    stock     DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK (stock >= 0)
);

-- 3) one row per bill
CREATE TABLE bills (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    bill_date      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    customer_name  VARCHAR(100) NOT NULL DEFAULT 'Walk-in',
    total          DECIMAL(10,2) NOT NULL,
    cashier_id     INT NOT NULL,
    FOREIGN KEY (cashier_id) REFERENCES users(id)
);

-- 4) the products inside each bill
CREATE TABLE bill_items (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    bill_id     INT NOT NULL,
    product_id  INT NOT NULL,
    qty         DECIMAL(10,2) NOT NULL CHECK (qty > 0),
    price       DECIMAL(10,2) NOT NULL,              -- price at the time of sale
    FOREIGN KEY (bill_id)    REFERENCES bills(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- starter data. Passwords: owner / admin123  and  cashier / cash123
INSERT INTO users (username, password, role) VALUES
 ('owner',   SHA2('admin123', 256), 'owner'),
 ('cashier', SHA2('cash123', 256),  'cashier');

INSERT INTO products (name, category, price, stock) VALUES
 ('Basmati Rice',  'Rice',   110, 200),
 ('Sona Masoori',  'Rice',    62, 300),
 ('Wheat',         'Wheat',   32, 400),
 ('Atta (Flour)',  'Wheat',   40, 250),
 ('Toor Dal',      'Pulses', 150, 120),
 ('Moong Dal',     'Pulses', 120,  90),
 ('Chana Dal',     'Pulses',  95,  15),
 ('Sugar',         'Others',  45, 180);
