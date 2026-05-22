import sqlite3

print("🔧 Creating city account...")

conn = sqlite3.connect("database.db")
cur = conn.cursor()

# ensure table exists
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT,
    role TEXT
)
""")

# insert city admin
cur.execute("""
INSERT OR IGNORE INTO users (username, password, role)
VALUES ('cityadmin', '123', 'city')
""")

conn.commit()
conn.close()

print("✅ City account ready (cityadmin / 123)")