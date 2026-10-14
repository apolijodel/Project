"""
Create the local MySQL database and load data into it.

    python setup_db.py            # create database + tables, migrate database.db (first run only)
    python setup_db.py --demo     # also add 26 weeks of generated demand history (for forecasting demos)
    python setup_db.py --reset    # DROP the MySQL database and rebuild it from scratch

Connection settings come from config.py (XAMPP defaults: root, no password, port 3306).
"""
import argparse
import random
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql
from werkzeug.security import generate_password_hash

import migrations
from config import BASE_DIR, Config
from db import connect

SCHEMA_FILE = BASE_DIR / "database" / "schema.sql"
SQLITE_FILE = BASE_DIR / "database.db"
DEMO_MARK = "Generated demo history"

DEFAULT_BARANGAYS = [
    "Bagong Ilog", "Kapitolyo", "Ugong", "Oranbo",
    "San Antonio", "Rosario", "Pinagbuhatan", "Caniogan",
]
DEFAULT_MEDICINES = [
    ("Paracetamol 500mg", 600), ("Paracetamol 250mg", 300), ("Ibuprofen 200mg", 250),
    ("Ibuprofen 400mg", 180), ("Amoxicillin 250mg", 160), ("Amoxicillin 500mg", 220),
    ("Metformin 500mg", 300), ("Metformin 850mg", 140), ("Cetirizine 10mg", 260),
    ("Loratadine 10mg", 200), ("Salbutamol 2mg", 90), ("Salbutamol 4mg", 70),
    ("Omeprazole 20mg", 190), ("Omeprazole 40mg", 110), ("Amlodipine 5mg", 320),
    ("Amlodipine 10mg", 150), ("Losartan 50mg", 340), ("Losartan 100mg", 120),
    ("Azithromycin 250mg", 80), ("Azithromycin 500mg", 60),
]


def config_dict():
    return {k: getattr(Config, k) for k in dir(Config) if k.isupper()}


def schema_statements():
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt or re.match(r"(CREATE DATABASE|USE)\b", stmt, re.I):
            continue
        yield stmt


def create_database(cfg, reset):
    server = connect(cfg, database=False)
    with server.cursor() as cur:
        if reset:
            cur.execute(f"DROP DATABASE IF EXISTS `{cfg['DB_NAME']}`")
            print(f"- dropped database {cfg['DB_NAME']}")
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{cfg['DB_NAME']}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    server.close()

    conn = connect(cfg)
    with conn.cursor() as cur:
        for stmt in schema_statements():
            cur.execute(stmt)
    print(f"- database `{cfg['DB_NAME']}` and tables are ready")
    return conn


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip()[:19], fmt)
        except ValueError:
            continue
    return None


def migrate_sqlite(conn, path):
    """Copy the old SQLite data, fixing duplicates, orphans and plain-text passwords."""
    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    skipped = []

    def cols(table):
        return {r[1] for r in src.execute(f"PRAGMA table_info({table})")}

    conn.begin()
    with conn.cursor() as cur:
        # barangays: merge duplicate names
        barangay_map, by_name = {}, {}
        for b in src.execute("SELECT id, name FROM barangays ORDER BY id"):
            name = (b["name"] or "").strip()
            if not name:
                skipped.append(f"barangay #{b['id']} (no name)")
                continue
            key = name.lower()
            if key in by_name:
                barangay_map[b["id"]] = by_name[key]
                skipped.append(f"barangay #{b['id']} '{name}' (duplicate, merged)")
                continue
            cur.execute("INSERT INTO barangays (id, name) VALUES (%s, %s)", (b["id"], name))
            barangay_map[b["id"]] = by_name[key] = b["id"]

        # medicines
        medicine_ids, med_names = set(), set()
        for m in src.execute("SELECT id, name, stock FROM medicines ORDER BY id"):
            name = (m["name"] or "").strip()
            if not name or name.lower() in med_names:
                skipped.append(f"medicine #{m['id']} '{name}' (blank or duplicate)")
                continue
            cur.execute(
                "INSERT INTO medicines (id, name, stock) VALUES (%s, %s, %s)",
                (m["id"], name, max(int(m["stock"] or 0), 0)),
            )
            medicine_ids.add(m["id"])
            med_names.add(name.lower())

        # users: unique usernames, hashed passwords, valid barangay links
        has_bid = "barangay_id" in cols("users")
        seen_users = set()
        for u in src.execute("SELECT * FROM users ORDER BY id"):
            username = (u["username"] or "").strip()
            role = (u["role"] or "").strip().lower()
            bid = barangay_map.get(u["barangay_id"]) if has_bid else None
            if role == "barangay" and bid is None:
                bid = by_name.get(username.lower())
            if not username or username.lower() in seen_users or role not in ("city", "barangay"):
                skipped.append(f"user #{u['id']} '{username}' (blank, duplicate or bad role)")
                continue
            if role == "barangay" and bid is None:
                skipped.append(f"user #{u['id']} '{username}' (barangay not found)")
                continue
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, barangay_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (u["id"], username, generate_password_hash(u["password"] or ""),
                 role, bid if role == "barangay" else None),
            )
            seen_users.add(username.lower())

        # requests
        rcols = cols("requests")
        statuses = {"pending", "approved", "released", "rejected", "cancelled"}
        count = 0
        for r in src.execute("SELECT * FROM requests ORDER BY id"):
            bid = barangay_map.get(r["barangay_id"])
            when = parse_dt(r["date"])
            qty = int(r["quantity"] or 0)
            status = (r["status"] or "pending").lower()
            if bid is None or r["medicine_id"] not in medicine_ids or when is None or qty <= 0 \
                    or status not in statuses:
                skipped.append(f"request #{r['id']} (missing barangay/medicine/date or bad value)")
                continue
            cur.execute(
                "INSERT INTO requests (id, barangay_id, medicine_id, quantity, status, "
                "requested_at, action_by, action_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (r["id"], bid, r["medicine_id"], qty, status, when,
                 r["action_by"] if "action_by" in rcols else None,
                 parse_dt(r["action_date"]) if "action_date" in rcols else None),
            )
            count += 1

        # barangay inventory: keep only medicines actually held (stock > 0)
        inv = 0
        if "barangay_inventory" in tables:
            for i in src.execute("SELECT * FROM barangay_inventory"):
                bid = barangay_map.get(i["barangay_id"])
                stock = int(i["stock"] or 0)
                if bid is None or i["medicine_id"] not in medicine_ids or stock <= 0:
                    continue
                cur.execute(
                    "INSERT INTO barangay_inventory (barangay_id, medicine_id, stock) "
                    "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE stock = stock + VALUES(stock)",
                    (bid, i["medicine_id"], stock),
                )
                inv += 1
    conn.commit()
    src.close()

    print(f"- migrated {len(by_name)} barangays, {len(medicine_ids)} medicines, "
          f"{len(seen_users)} users, {count} requests, {inv} inventory rows from {path.name}")
    if skipped:
        print(f"- skipped/merged {len(skipped)} bad rows:")
        for s in skipped:
            print(f"    - {s}")
    print("- existing passwords were kept but are now stored as secure hashes")


def seed_defaults(conn):
    conn.begin()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'city')",
            ("cityadmin", generate_password_hash("admin123")),
        )
        for name in DEFAULT_BARANGAYS:
            cur.execute("INSERT INTO barangays (name) VALUES (%s)", (name,))
            cur.execute(
                "INSERT INTO users (username, password_hash, role, barangay_id) "
                "VALUES (%s, %s, 'barangay', %s)",
                (name, generate_password_hash("barangay123"), cur.lastrowid),
            )
        cur.executemany("INSERT INTO medicines (name, stock) VALUES (%s, %s)", DEFAULT_MEDICINES)
    conn.commit()
    print("- seeded default data")
    print("    city login:      cityadmin / admin123")
    print("    barangay login:  <barangay name> / barangay123   (e.g. Kapitolyo / barangay123)")


def seed_mock_users(conn):
    """Create the demo accounts from config.py (one per user level). Existing accounts are left alone."""
    created = []
    conn.begin()
    with conn.cursor() as cur:
        for acc in Config.DEMO_ACCOUNTS:
            cur.execute("SELECT id FROM users WHERE username = %s", (acc["username"],))
            if cur.fetchone():
                continue
            barangay_id = None
            if acc["role"] == "barangay":
                cur.execute("SELECT id FROM barangays WHERE name = %s", (acc["barangay"],))
                row = cur.fetchone()
                if row:
                    barangay_id = row["id"]
                else:
                    cur.execute("INSERT INTO barangays (name) VALUES (%s)", (acc["barangay"],))
                    barangay_id = cur.lastrowid
            cur.execute(
                "INSERT INTO users (username, password_hash, role, barangay_id) VALUES (%s, %s, %s, %s)",
                (acc["username"], generate_password_hash(acc["password"]), acc["role"], barangay_id),
            )
            created.append(acc)
    conn.commit()
    if created:
        print("- demo accounts created:")
        for acc in created:
            print(f"    {acc['label']:<24} {acc['username']} / {acc['password']}")


def seed_demo(conn, weeks=26, seed=7):
    """Add realistic weekly demand so the ARIMA forecasts have something to learn from."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM requests WHERE remarks = %s", (DEMO_MARK,))
        if cur.fetchone()["n"]:
            print("- demo history already present, skipping")
            return
        cur.execute("SELECT id FROM barangays")
        barangays = [r["id"] for r in cur.fetchall()]
        cur.execute("SELECT id FROM medicines WHERE is_active = 1")
        medicines = [r["id"] for r in cur.fetchall()]
    if not barangays or not medicines:
        print("- no barangays/medicines to generate demo data for")
        return

    rng = random.Random(seed)
    today = date.today()
    start = today - timedelta(weeks=weeks)
    profile = {m: (rng.uniform(4, 18), rng.uniform(-0.15, 0.35)) for m in medicines}
    rows = []

    for bid in barangays:
        size = rng.uniform(0.6, 1.5)  # bigger barangays request more
        regular = rng.sample(medicines, k=min(len(medicines), rng.randint(6, 10)))
        for week in range(weeks):
            monday = start + timedelta(weeks=week)
            for mid in regular:
                if rng.random() < 0.35:  # not every medicine every week
                    continue
                base, trend = profile[mid]
                seasonal = 1 + 0.25 * ((week % 13) / 13)  # mild quarterly swing
                qty = max(1, round((base + trend * week) * size * seasonal + rng.gauss(0, 3)))
                when = datetime.combine(monday + timedelta(days=rng.choice([1, 2, 4])),
                                        datetime.min.time()) + timedelta(hours=rng.randint(8, 16))
                age = (today - when.date()).days
                if age > 10:
                    status = "released" if rng.random() > 0.06 else "rejected"
                elif age > 4:
                    status = rng.choice(["released", "approved"])
                else:
                    status = "pending"
                action_at = when + timedelta(hours=rng.randint(2, 30)) if status != "pending" else None
                rows.append((bid, mid, qty, status, when,
                             "cityadmin" if action_at else None, action_at, DEMO_MARK))

    conn.begin()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO requests (barangay_id, medicine_id, quantity, status, requested_at, "
            "action_by, action_at, remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            rows,
        )
        # barangays hold part of what they recently received
        cur.execute(
            """
            SELECT barangay_id, medicine_id, SUM(quantity) AS qty
            FROM requests
            WHERE status = 'released' AND remarks = %s AND requested_at >= %s
            GROUP BY barangay_id, medicine_id
            """,
            (DEMO_MARK, today - timedelta(weeks=3)),
        )
        for r in cur.fetchall():
            cur.execute(
                "INSERT INTO barangay_inventory (barangay_id, medicine_id, stock) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE stock = VALUES(stock)",
                (r["barangay_id"], r["medicine_id"], int(int(r["qty"]) * rng.uniform(0.05, 0.6))),
            )
        # vary city stock so the decision table shows every risk level
        for mid in medicines:
            cur.execute("UPDATE medicines SET stock = %s WHERE id = %s",
                        (rng.choice([0, 15, 40, 90, 160, 260, 400, 650]), mid))
    conn.commit()
    print(f"- added {len(rows)} demo requests over the last {weeks} weeks")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop and rebuild the MySQL database")
    parser.add_argument("--demo", action="store_true", help="add generated demand history")
    parser.add_argument("--sqlite", type=Path, default=SQLITE_FILE, help="old SQLite file to migrate")
    args = parser.parse_args()

    cfg = config_dict()
    print(f"Connecting to MySQL at {cfg['DB_HOST']}:{cfg['DB_PORT']} as {cfg['DB_USER']} ...")
    try:
        conn = create_database(cfg, args.reset)
    except pymysql.err.OperationalError as e:
        print(f"\nCould not connect to MySQL: {e}")
        print("Start MySQL in the XAMPP Control Panel, then run this again.")
        sys.exit(1)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        empty = cur.fetchone()["n"] == 0

    if empty:
        if args.sqlite.exists():
            migrate_sqlite(conn, args.sqlite)
        else:
            seed_defaults(conn)
    else:
        print("- database already has data, nothing migrated")

    seed_mock_users(conn)

    if args.demo:
        seed_demo(conn)

    for change in migrations.upgrade(conn):
        print(f"- schema upgraded: {change}")

    conn.close()
    print("Done. Start the app with:  python app.py")


if __name__ == "__main__":
    main()
