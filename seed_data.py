import sqlite3
import random
from datetime import datetime, timedelta

conn = sqlite3.connect("database.db")
cur = conn.cursor()

cur.execute("DELETE FROM requests")

barangays = cur.execute("SELECT id FROM barangays").fetchall()
medicines = cur.execute("SELECT id FROM medicines").fetchall()

if not barangays or not medicines:
    print("Missing data")
    exit()

start_date = datetime(2025, 2, 1)
end_date = datetime(2025, 3, 15)

current = start_date

while current <= end_date:

    # 👉 only 2 days per week (realistic schedule)
    if current.weekday() in [1, 4]:  # Tuesday, Friday

        for b in barangays:

            # 👉 ONLY 1–2 medicines per barangay per week
            chosen_meds = random.sample(medicines, k=random.randint(1, 2))

            for m in chosen_meds:

                base = 20
                trend = (current - start_date).days * 0.4
                noise = random.randint(-5, 6)

                quantity = max(int(base + trend + noise), 1)

                cur.execute("""
                    INSERT INTO requests (
                        barangay_id,
                        medicine_id,
                        quantity,
                        status,
                        date
                    ) VALUES (?, ?, ?, 'released', ?)
                """, (
                    b[0],
                    m[0],
                    quantity,
                    current.strftime("%Y-%m-%d")
                ))

    current += timedelta(days=1)

conn.commit()
conn.close()

print("✅ Minimal realistic dataset created!")