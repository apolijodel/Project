import sqlite3

print("🔧 Creating full database...")

conn = sqlite3.connect("database.db")
cur = conn.cursor()

# ================= USERS (CITY) =================
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT,
    role TEXT
)
""")

# ================= BARANGAYS =================
cur.execute("""
CREATE TABLE IF NOT EXISTS barangays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    password TEXT
)
""")

# ================= MEDICINES =================
cur.execute("""
CREATE TABLE IF NOT EXISTS medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    stock INTEGER DEFAULT 0
)
""")

# ================= REQUESTS =================
cur.execute("""
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barangay_id INTEGER,
    medicine_id INTEGER,
    quantity INTEGER,
    status TEXT DEFAULT 'pending',
    date TEXT
)
""")

# ================= SAMPLE DATA =================

# city account
cur.execute("""
INSERT OR IGNORE INTO users (id, username, password, role)
VALUES (1, 'cityadmin', '123', 'city')
""")

# barangays (PASIG example)
barangays = [
    ("Bagong Ilog", "123"),
    ("Kapitolyo", "123"),
    ("Ugong", "123"),
    ("Oranbo", "123"),
    ("San Antonio", "123")
]

for b in barangays:
    cur.execute("""
    INSERT INTO barangays (name, password)
    VALUES (?, ?)
    """, b)

# medicines
medicines = [
    ("Paracetamol 250mg", 120),
    ("Paracetamol 500mg", 200),
    ("Ibuprofen 200mg", 150),
    ("Ibuprofen 400mg", 90),
    ("Amoxicillin 250mg", 80),
    ("Amoxicillin 500mg", 60),
    ("Metformin 500mg", 110),
    ("Metformin 850mg", 70),
    ("Cetirizine 10mg", 140),
    ("Loratadine 10mg", 100),
    ("Salbutamol 2mg", 50),
    ("Salbutamol 4mg", 40),
    ("Omeprazole 20mg", 95),
    ("Omeprazole 40mg", 60),
    ("Amlodipine 5mg", 130),
    ("Amlodipine 10mg", 85),
    ("Losartan 50mg", 120),
    ("Losartan 100mg", 75),
    ("Azithromycin 250mg", 60),
    ("Azithromycin 500mg", 45)
]

for m in medicines:
    cur.execute("""
    INSERT INTO medicines (name, stock)
    VALUES (?, ?)
    """, m)

conn.commit()
conn.close()

print("✅ DATABASE READY (FULL SYSTEM BUILT)")