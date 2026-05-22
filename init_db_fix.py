import sqlite3

conn = sqlite3.connect("database.db")
cur = conn.cursor()

print("🔧 Setting up CLEAN Barangay Login System...")

# ================= BARANGAYS =================
cur.execute("""
CREATE TABLE IF NOT EXISTS barangays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    password TEXT
)
""")

# ================= USERS (OPTIONAL REMOVE LATER) =================
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT,
    role TEXT,
    barangay_id INTEGER
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

# ================= PASIG BARANGAYS =================
barangays = [
    "Bagong Ilog",
    "Kapitolyo",
    "Ugong",
    "Rosario",
    "San Miguel",
    "Buting",
    "Caniogan",
    "Kalawaan",
    "Pinagbuhatan"
]

existing = cur.execute("SELECT COUNT(*) FROM barangays").fetchone()[0]

if existing == 0:
    for b in barangays:
        cur.execute("""
            INSERT INTO barangays (name, password)
            VALUES (?, '123')
        """, (b,))
    print("✔ Barangays inserted with default password = 123")
else:
    print("⚠ Barangays already exist")

conn.commit()
conn.close()

print("✅ CLEAN SYSTEM READY")