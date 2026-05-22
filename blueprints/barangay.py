from flask import Blueprint, render_template, session, redirect, url_for, request
import sqlite3

barangay_bp = Blueprint("barangay", __name__, url_prefix="/barangay")


def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


# ================= DASHBOARD =================
@barangay_bp.route("/")
def dashboard():

    if session.get("role") != "barangay":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    barangay_id = session.get("barangay_id")

    # ---------------- INVENTORY (FIXED) ----------------
    inventory = cur.execute("""
    SELECT
        m.name,
        COALESCE(bi.stock, 0) AS stock
    FROM medicines m
    LEFT JOIN barangay_inventory bi
        ON m.id = bi.medicine_id
        AND bi.barangay_id = ?
    """, (session["barangay_id"],)).fetchall()

    inventory_labels = [i["name"] for i in inventory]
    inventory_values = [i["stock"] for i in inventory]
    # low stock alert
    low_stock = [i for i in inventory if i["stock"] <= 20]
    out_of_stock = [i for i in inventory if i["stock"] == 0]

    # ---------------- RECENT REQUESTS ----------------
    requests = cur.execute("""
    SELECT m.name as medicine,
           r.quantity,
           r.status,
           r.date
    FROM requests r
    JOIN medicines m ON m.id = r.medicine_id
    WHERE r.barangay_id = ?   -- 👈 THIS IS STEP 4
    ORDER BY r.date DESC
    LIMIT 5
    """, (session["barangay_id"],)).fetchall()

    # ---------------- STATUS SUMMARY ----------------
    status_counts = cur.execute("""
    SELECT status, COUNT(*) as count
    FROM requests
    WHERE barangay_id = ?   -- 👈 THIS IS STEP 4
    GROUP BY status
    """, (session["barangay_id"],)).fetchall()

    conn.close()

    return render_template(
        "barangay/barangay_dashboard.html",
        username=session["username"],
        inventory=inventory,
        inventory_labels=inventory_labels,   # send to chart
        inventory_values=inventory_values, 
        low_stock=low_stock,
        requests=requests,
        status_counts=status_counts
    )


# ================= REQUEST SUPPLIES =================
@barangay_bp.route("/request", methods=["GET", "POST"])
def request_supplies():

    if session.get("role") != "barangay":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    medicines = cur.execute("""
    SELECT m.id,
           m.name,
           COALESCE(bi.stock, 0) AS stock
    FROM medicines m
    LEFT JOIN barangay_inventory bi
        ON m.id = bi.medicine_id
        AND bi.barangay_id = ?
    """, (session["barangay_id"],)).fetchall()

    barangay_id = session.get("barangay_id")

    # ---------------- INSERT REQUEST ----------------
    if request.method == "POST":

        cur.execute("""
    INSERT INTO requests (barangay_id, medicine_id, quantity, status, date)
    VALUES (?, ?, ?, 'pending', datetime('now'))
    """, (
    session["barangay_id"],  # 👈 STEP 4 USED HERE TOO
    request.form["medicine_id"],
    request.form["quantity"]
    ))

        conn.commit()

    # ---------------- HISTORY ----------------
    history = cur.execute("""
        SELECT m.name AS medicine,
               r.quantity,
               r.status,
               r.date
        FROM requests r
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.barangay_id = ?
        ORDER BY r.date DESC
    """, (barangay_id,)).fetchall()

    conn.close()

    return render_template(
        "barangay/request_supplies.html",
        medicines=medicines,
        history=history,
        username=session["username"]
    )


# ================= INVENTORY PAGE =================
@barangay_bp.route("/inventory", methods=["GET", "POST"])
def inventory():

    if session.get("role") != "barangay":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    # Deduct stock
    if request.method == "POST":
        medicine_id = request.form["medicine_id"]
        quantity = int(request.form["quantity"])

        current_stock = cur.execute("""
            SELECT stock
            FROM barangay_inventory
            WHERE barangay_id = ? AND medicine_id = ?
        """, (
            session["barangay_id"],
            medicine_id
        )).fetchone()

        if current_stock and current_stock["stock"] >= quantity:
            cur.execute("""
                UPDATE barangay_inventory
                SET stock = stock - ?
                WHERE barangay_id = ? AND medicine_id = ?
            """, (
                quantity,
                session["barangay_id"],
                medicine_id
            ))
            conn.commit()

    # Search
    search = request.args.get("search", "")

    data = cur.execute("""
        SELECT
            m.id,
            m.name,
            bi.stock
        FROM barangay_inventory bi
        JOIN medicines m ON m.id = bi.medicine_id
        WHERE bi.barangay_id = ?
        AND m.name LIKE ?
        ORDER BY m.name ASC
    """, (
        session["barangay_id"],
        f"%{search}%"
    )).fetchall()

    conn.close()

    return render_template(
        "barangay/barangay_inventory.html",
        inventory=data,
        username=session["username"],
        search=search
    )