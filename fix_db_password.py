import sqlite3

conn = sqlite3.connect("database.db")
cur = conn.cursor()

print("🔧 Fixing database...")

# ================= ADD PASSWORD COLUMN =================
try:
    cur.execute("ALTER TABLE barangays ADD COLUMN password TEXT")
    print("✔ password column added")
except Exception as e:
    print("⚠ password already exists or error:", e)

# ================= SET DEFAULT PASSWORD =================
cur.execute("""
UPDATE barangays
SET password = '123'
WHERE password IS NULL
""")

conn.commit()
conn.close()

print("✅ DONE FIXING PASSWORD COLUMN")