"""
Script to seed a sample relational dataset (E-commerce / Northwind style) in PostgreSQL.
"""

import argparse
import sys
import psycopg2


SAMPLE_SCHEMA_SQL = """
-- Drop existing tables
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS categories CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS employees CASCADE;

-- Categories
CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    category_name VARCHAR(100) NOT NULL,
    description TEXT
);

-- Products
CREATE TABLE products (
    product_id SERIAL PRIMARY KEY,
    product_name VARCHAR(150) NOT NULL,
    category_id INTEGER REFERENCES categories(category_id),
    unit_price NUMERIC(10, 2) NOT NULL,
    units_in_stock INTEGER NOT NULL DEFAULT 0,
    discontinued BOOLEAN NOT NULL DEFAULT FALSE
);

-- Customers
CREATE TABLE customers (
    customer_id VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(150) NOT NULL,
    contact_name VARCHAR(100),
    country VARCHAR(50),
    city VARCHAR(50)
);

-- Employees
CREATE TABLE employees (
    employee_id SERIAL PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    title VARCHAR(100),
    hire_date DATE,
    department VARCHAR(50)
);

-- Orders
CREATE TABLE orders (
    order_id SERIAL PRIMARY KEY,
    customer_id VARCHAR(10) REFERENCES customers(customer_id),
    employee_id INTEGER REFERENCES employees(employee_id),
    order_date DATE NOT NULL,
    ship_country VARCHAR(50),
    freight NUMERIC(10, 2) DEFAULT 0.00
);

-- Order Items
CREATE TABLE order_items (
    order_id INTEGER REFERENCES orders(order_id),
    product_id INTEGER REFERENCES products(product_id),
    unit_price NUMERIC(10, 2) NOT NULL,
    quantity INTEGER NOT NULL,
    discount NUMERIC(4, 2) DEFAULT 0.00,
    PRIMARY KEY (order_id, product_id)
);
"""

SAMPLE_DATA_SQL = """
-- Insert Categories
INSERT INTO categories (category_name, description) VALUES
('Beverages', 'Soft drinks, coffees, teas, beers, and ales'),
('Condiments', 'Sweet and savory sauces, relishes, spreads, and seasonings'),
('Confections', 'Desserts, candies, and sweet breads'),
('Dairy Products', 'Cheeses, butter, milk'),
('Grains & Cereals', 'Breads, crackers, pasta, and cereal'),
('Meat & Poultry', 'Prepared meats'),
('Produce', 'Dried fruit and bean curd'),
('Seafood', 'Seaweed and fish');

-- Insert Products
INSERT INTO products (product_name, category_id, unit_price, units_in_stock, discontinued) VALUES
('Chai', 1, 18.00, 39, FALSE),
('Chang', 1, 19.00, 17, FALSE),
('Aniseed Syrup', 2, 10.00, 13, FALSE),
('Chef Anton''s Cajun Seasoning', 2, 22.00, 53, FALSE),
('Chef Anton''s Gumbo Mix', 2, 21.35, 0, TRUE),
('Grandma''s Boysenberry Spread', 2, 25.00, 120, FALSE),
('Uncle Bob''s Organic Dried Pears', 7, 30.00, 15, FALSE),
('Northwoods Cranberry Sauce', 2, 40.00, 6, FALSE),
('Mishi Kobe Niku', 6, 97.00, 29, TRUE),
('Ikura', 8, 31.00, 31, FALSE),
('Queso Cabrales', 4, 21.00, 22, FALSE),
('Queso Manchego La Pastora', 4, 38.00, 86, FALSE),
('Konbu', 8, 6.00, 24, FALSE),
('Tofu', 7, 23.25, 35, FALSE),
('Genen Shouyu', 2, 15.50, 39, FALSE),
('Pavlova', 3, 17.45, 29, FALSE),
('Alice Mutton', 6, 39.00, 0, TRUE),
('Carnarvon Tigers', 8, 62.50, 42, FALSE),
('Teatime Chocolate Biscuits', 3, 9.20, 25, FALSE),
('Sir Rodney''s Marmalade', 3, 81.00, 40, FALSE);

-- Insert Customers
INSERT INTO customers (customer_id, company_name, contact_name, country, city) VALUES
('ALFKI', 'Alfreds Futterkiste', 'Maria Anders', 'Germany', 'Berlin'),
('ANATR', 'Ana Trujillo Emparedados y helados', 'Ana Trujillo', 'Mexico', 'Mexico D.F.'),
('ANTON', 'Antonio Moreno Taquería', 'Antonio Moreno', 'Mexico', 'Mexico D.F.'),
('AROUT', 'Around the Horn', 'Thomas Hardy', 'UK', 'London'),
('BERGS', 'Berglunds snabbköp', 'Christina Berglund', 'Sweden', 'Luleå'),
('BLAUS', 'Blauer See Delikatessen', 'Hanna Moos', 'Germany', 'Mannheim'),
('BLONP', 'Blondel père et fils', 'Frédérique Citeaux', 'France', 'Strasbourg'),
('BOLID', 'Bólido Comidas preparadas', 'Martín Sommer', 'Spain', 'Madrid'),
('BONAP', 'Bon app''', 'Laurence Lebihan', 'France', 'Marseille'),
('BOTTM', 'Bottom-Dollar Markets', 'Elizabeth Lincoln', 'Canada', 'Tsawwassen');

-- Insert Employees
INSERT INTO employees (first_name, last_name, title, hire_date, department) VALUES
('Nancy', 'Davolio', 'Sales Representative', '2020-05-01', 'Sales'),
('Andrew', 'Fuller', 'Vice President, Sales', '2018-08-14', 'Sales'),
('Janet', 'Leverling', 'Sales Representative', '2021-04-01', 'Sales'),
('Margaret', 'Peacock', 'Sales Representative', '2019-05-03', 'Sales'),
('Steven', 'Buchanan', 'Sales Manager', '2017-10-17', 'Sales');

-- Insert Orders
INSERT INTO orders (customer_id, employee_id, order_date, ship_country, freight) VALUES
('ALFKI', 1, '2023-01-15', 'Germany', 29.46),
('ANATR', 3, '2023-02-10', 'Mexico', 11.61),
('ANTON', 4, '2023-02-18', 'Mexico', 65.83),
('AROUT', 1, '2023-03-05', 'UK', 41.34),
('BERGS', 2, '2023-03-22', 'Sweden', 8.53),
('BLAUS', 5, '2023-04-01', 'Germany', 55.09),
('BLONP', 3, '2023-04-14', 'France', 3.05),
('BOLID', 4, '2023-05-02', 'Spain', 100.19),
('BONAP', 1, '2023-05-19', 'France', 23.63),
('BOTTM', 2, '2023-06-08', 'Canada', 89.00);

-- Insert Order Items
INSERT INTO order_items (order_id, product_id, unit_price, quantity, discount) VALUES
(1, 1, 18.00, 10, 0.0),
(1, 2, 19.00, 5, 0.05),
(2, 3, 10.00, 20, 0.0),
(3, 4, 22.00, 15, 0.1),
(4, 6, 25.00, 8, 0.0),
(5, 7, 30.00, 12, 0.0),
(6, 11, 21.00, 25, 0.15),
(7, 12, 38.00, 6, 0.0),
(8, 16, 17.45, 18, 0.05),
(9, 18, 62.50, 4, 0.0),
(10, 19, 9.20, 50, 0.2);
"""


def seed_database(host: str, port: int, dbname: str, user: str, password: str):
    print(f"Connecting to '{dbname}' on {host}:{port} as '{user}'...")
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
        )
        conn.autocommit = True
        cur = conn.cursor()

        print("Executing schema DDL...")
        cur.execute(SAMPLE_SCHEMA_SQL)

        print("Inserting sample records...")
        cur.execute(SAMPLE_DATA_SQL)

        cur.close()
        conn.close()
        print("Database seeded successfully with sample dataset.")

    except Exception as e:
        print(f"Error seeding database: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed sample relational database.")
    parser.add_argument("--host", default="localhost", help="Postgres host")
    parser.add_argument("--port", type=int, default=5432, help="Postgres port")
    parser.add_argument("--dbname", default="text_to_sql_db", help="Database name")
    parser.add_argument("--user", default="postgres", help="Database user")
    parser.add_argument("--password", default="postgres", help="Database password")

    args = parser.parse_args()
    seed_database(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )
