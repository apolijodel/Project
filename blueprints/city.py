from flask import Blueprint, render_template, session, redirect, url_for, request
import sqlite3
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from services.forecast import forecast_medicine_usage

city_bp = Blueprint("city", __name__, url_prefix="/city")


# ================= DATABASE =================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


# ================= DASHBOARD =================
@city_bp.route("/")
def dashboard():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    data = cur.execute("""
        SELECT name, stock
        FROM medicines
    """).fetchall()

    labels = [d["name"] for d in data]
    values = [d["stock"] for d in data]

    total_barangays = cur.execute("""
        SELECT COUNT(*) FROM barangays
    """).fetchone()[0]

    total_requests = cur.execute("""
        SELECT COUNT(*) FROM requests
    """).fetchone()[0]

    pending_approvals = cur.execute("""
        SELECT COUNT(*)
        FROM requests
        WHERE status = 'pending'
    """).fetchone()[0]

    total_supplies = cur.execute("""
        SELECT COALESCE(SUM(quantity),0)
        FROM requests
        WHERE status = 'released'
    """).fetchone()[0]

    history = cur.execute("""
        SELECT 
            b.name as barangay,
            m.name as medicine,
            r.quantity,
            r.date
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.status = 'released'
        ORDER BY r.date DESC
    """).fetchall()

    conn.close()

    return render_template(
        "city/city_dashboard.html",
        username=session.get("username", ""),
        chart_labels=labels or [],
        chart_values=values or [],
        total_barangays=total_barangays,
        total_requests=total_requests,
        pending_approvals=pending_approvals,
        total_supplies=total_supplies,
        history=history or []
    )


# ================= BARANGAY DETAILS =================
@city_bp.route("/barangay/<int:id>")
def barangay_detail(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    barangay = cur.execute("""
        SELECT *
        FROM barangays
        WHERE id = ?
    """, (id,)).fetchone()

    medicines = cur.execute("""
        SELECT 
            m.name,
            COALESCE(SUM(r.quantity), 0) as total
        FROM medicines m
        LEFT JOIN requests r 
            ON m.id = r.medicine_id
            AND r.barangay_id = ?
        GROUP BY m.id
    """, (id,)).fetchall()

    chart_labels = [m["name"] for m in medicines]
    chart_values = [m["total"] for m in medicines]

    history = cur.execute("""
        SELECT
            m.name as medicine,
            r.quantity,
            r.status,
            r.date
        FROM requests r
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.barangay_id = ?
        ORDER BY r.date DESC
    """, (id,)).fetchall()

    conn.close()

    return render_template(
        "city/barangay_detail.html",
        username=session.get("username", ""),
        barangay=barangay,
        medicines=medicines,
        chart_labels=chart_labels,
        chart_values=chart_values,
        history=history
    )


# ================= REPORTS =================
@city_bp.route("/reports")
def reports():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    medicines = cur.execute("""
        SELECT id, name
        FROM medicines
    """).fetchall()

    chart_labels = []
    chart_actual = []
    chart_forecast = []
    risk_levels = []
    recommendations = []

    for m in medicines:

        actual = cur.execute("""
            SELECT COALESCE(SUM(quantity), 0)
            FROM requests
            WHERE medicine_id = ?
        """, (m["id"],)).fetchone()[0]

        forecast = int(actual * 1.1)

        if actual < 50:
            risk = "Low"
        elif actual < 150:
            risk = "Medium"
        else:
            risk = "High"

        rec = int(forecast * (1.3 if risk == "High" else 1.1))

        chart_labels.append(m["name"])
        chart_actual.append(actual)
        chart_forecast.append(forecast)
        risk_levels.append(risk)
        recommendations.append(rec)

    conn.close()

    return render_template(
        "city/reports.html",
        username=session.get("username", ""),
        chart_labels=chart_labels,
        chart_actual=chart_actual,
        chart_forecast=chart_forecast,
        risk_levels=risk_levels,
        recommendations=recommendations
    )


# ================= SUPPLY =================
@city_bp.route("/supply", methods=["GET", "POST"])
def supply():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    barangays = cur.execute("""
        SELECT *
        FROM barangays
    """).fetchall()

    medicines = cur.execute("""
        SELECT *
        FROM medicines
    """).fetchall()

    recommendations = {}

    # ================= FORECAST =================
    for m in medicines:

        data = cur.execute("""
            SELECT date, quantity
            FROM requests
            WHERE medicine_id = ?
            AND status = 'released'
            ORDER BY date ASC
        """, (m["id"],)).fetchall()

        clean = [(d["date"], d["quantity"]) for d in data]

        forecast_values = forecast_medicine_usage(clean, steps=1)

        expected = int(forecast_values[0]) if forecast_values else 0

        stock = m["stock"]

        suggested = max(int(expected * 1.2), 0)

        recommendations[m["id"]] = {
            "name": m["name"],
            "stock": stock,
            "forecast": expected,
            "suggested": suggested
        }

    # ================= POST =================
    if request.method == "POST":

        barangay_id = request.form.get("barangay")
        medicine_id = request.form.get("medicine")
        quantity = request.form.get("quantity")

        if not barangay_id or not medicine_id or not quantity:
            conn.close()
            return "Missing form data"

        quantity = int(quantity)

        stock_row = cur.execute("""
            SELECT stock
            FROM medicines
            WHERE id = ?
        """, (medicine_id,)).fetchone()

        if not stock_row:
            conn.close()
            return "Medicine not found"

        stock = stock_row["stock"]

        if quantity > stock:
            conn.close()
            return "Not enough stock"

        cur.execute("""
            INSERT INTO requests (
                barangay_id,
                medicine_id,
                quantity,
                status,
                date
            )
            VALUES (?, ?, ?, 'released', datetime('now'))
        """, (barangay_id, medicine_id, quantity))

        cur.execute("""
            UPDATE medicines
            SET stock = stock - ?
            WHERE id = ?
        """, (quantity, medicine_id))

        conn.commit()

    history = cur.execute("""
        SELECT
            b.name as barangay,
            m.name as medicine,
            r.quantity,
            r.date
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.status = 'released'
        ORDER BY r.date DESC
    """).fetchall()

    conn.close()

    return render_template(
        "city/supply.html",
        username=session.get("username", ""),
        barangays=barangays,
        medicines=medicines,
        history=history,
        recommendations=recommendations
    )


# ================= ALL BARANGAYS =================
@city_bp.route("/barangays")
def all_barangays():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    data = cur.execute("""
        SELECT 
            b.id,
            b.name,
            COUNT(r.id) as total_requests,
            COALESCE(SUM(r.quantity), 0) as total_used,
            SUM(CASE WHEN r.status='released' THEN r.quantity ELSE 0 END) as total_released,
            SUM(CASE WHEN r.status='pending' THEN 1 ELSE 0 END) as pending_requests
        FROM barangays b
        LEFT JOIN requests r ON b.id = r.barangay_id
        GROUP BY b.id
    """).fetchall()

    conn.close()

    return render_template(
        "city/all_barangays.html",
        username=session.get("username", ""),
        barangays=data or []
    )


# ================= REQUESTS =================
@city_bp.route("/requests")
def view_requests():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    requests = cur.execute("""
        SELECT 
            r.id,
            b.name as barangay,
            m.name as medicine,
            r.quantity,
            r.status,
            r.date,
            r.action_by,
            r.action_date
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        ORDER BY r.date DESC
    """).fetchall()

    conn.close()

    return render_template(
        "city/requests.html",
        username=session.get("username"),
        requests=requests
    )


# ================= APPROVE =================
@city_bp.route("/approve/<int:id>")
def approve_request(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE requests
        SET status = 'approved',
            action_by = ?,
            action_date = datetime('now')
        WHERE id = ?
    """, (session["username"], id))

    conn.commit()
    conn.close()

    return redirect(url_for("city.view_requests"))


# ================= REJECT =================
@city_bp.route("/reject/<int:id>")
def reject_request(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE requests
        SET status = 'rejected',
            action_by = ?,
            action_date = datetime('now')
        WHERE id = ?
    """, (session["username"], id))

    conn.commit()
    conn.close()

    return redirect(url_for("city.view_requests"))


# ================= RELEASE =================
@city_bp.route("/release/<int:id>")
def release_request(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    # get request info
    req = cur.execute("""
        SELECT barangay_id, medicine_id, quantity
        FROM requests
        WHERE id = ?
    """, (id,)).fetchone()

    if req:

        # mark as released
        cur.execute("""
            UPDATE requests
            SET status = 'released',
                action_by = ?,
                action_date = datetime('now')
            WHERE id = ?
        """, (session["username"], id))

        # deduct from CITY inventory
        cur.execute("""
            UPDATE medicines
            SET stock = stock - ?
            WHERE id = ?
        """, (req["quantity"], req["medicine_id"]))

        # add to BARANGAY inventory
        inventory = cur.execute("""
            SELECT id, stock
            FROM barangay_inventory
            WHERE barangay_id = ?
            AND medicine_id = ?
        """, (req["barangay_id"], req["medicine_id"])).fetchone()

        if inventory:
            cur.execute("""
                UPDATE barangay_inventory
                SET stock = stock + ?
                WHERE id = ?
            """, (req["quantity"], inventory["id"]))
        else:
            cur.execute("""
                INSERT INTO barangay_inventory (
                    barangay_id,
                    medicine_id,
                    stock
                )
                VALUES (?, ?, ?)
            """, (
                req["barangay_id"],
                req["medicine_id"],
                req["quantity"]
            ))

    conn.commit()
    conn.close()

    return redirect(url_for("city.view_requests"))


# ================= REQUEST DETAILS =================
@city_bp.route("/request/<int:id>")
def request_details(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()

    request_data = conn.execute("""
        SELECT 
            r.*,
            m.stock,
            b.name AS barangay,
            m.name AS medicine
        FROM requests r
        JOIN medicines m ON r.medicine_id = m.id
        JOIN barangays b ON r.barangay_id = b.id
        WHERE r.id = ?
    """, (id,)).fetchone()

    history = conn.execute("""
        SELECT date, quantity
        FROM requests
        WHERE medicine_id = ?
        ORDER BY date ASC
    """, (request_data["medicine_id"],)).fetchall()

    conn.close()

    history_dates = [h["date"] for h in history]
    history_values = [h["quantity"] for h in history]

    prediction = 0

    # ================= ARIMA =================
    if len(history_values) >= 5:

        try:
            df = pd.DataFrame(history_values, columns=["quantity"])

            model = ARIMA(df["quantity"], order=(1,1,1))

            model_fit = model.fit()

            forecast_result = model_fit.forecast(steps=1)

            prediction = float(forecast_result.iloc[0])

        except:
            prediction = (
                sum(history_values[-3:]) / len(history_values[-3:])
            )

    else:

        prediction = (
            sum(history_values[-3:]) / len(history_values[-3:])
            if history_values else 0
        )

    return render_template(
        "city/request_details.html",
        req=request_data,
        prediction=prediction,
        stock=request_data["stock"],
        history_dates=history_dates,
        history_values=history_values
    )


# ================= USER MANAGEMENT =================
@city_bp.route("/users")
def manage_users():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    users = cur.execute("""
        SELECT id, username, role
        FROM users
        ORDER BY role, username
    """).fetchall()

    conn.close()

    return render_template(
        "city/users.html",
        username=session.get("username"),
        users=users
    )


# ================= ADD USER =================
@city_bp.route("/users/add", methods=["POST"])
def add_user():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    username = request.form.get("username")
    password = request.form.get("password")
    role = request.form.get("role")

    conn = get_db()
    cur = conn.cursor()

    # insert user
    cur.execute("""
        INSERT INTO users (username, password, role)
        VALUES (?, ?, ?)
    """, (username, password, role))

    # if barangay account → create barangay profile too
    if role == "barangay":

        cur.execute("""
            INSERT INTO barangays (name, password)
            VALUES (?, ?)
        """, (username, password))

    conn.commit()
    conn.close()

    return redirect(url_for("city.manage_users"))


# ================= DELETE USER =================
@city_bp.route("/users/delete/<int:id>")
def delete_user(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM users
        WHERE id = ?
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("city.manage_users"))

@city_bp.route("/add-stock", methods=["GET", "POST"])
def add_stock():

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    message = None

    # ================= HANDLE FORMS =================
    if request.method == "POST":

        # identify which form submitted
        action = request.form.get("action")

        # ================= ADD NEW MEDICINE =================
        if action == "add_medicine":

            medicine_name = request.form["medicine_name"]

            existing = cur.execute("""
                SELECT * FROM medicines
                WHERE name = ?
            """, (medicine_name,)).fetchone()

            if not existing:

                cur.execute("""
                    INSERT INTO medicines (name, stock)
                    VALUES (?, 0)
                """, (medicine_name,))

                conn.commit()
                message = "Medicine added successfully."

            else:
                message = "Medicine already exists."

        # ================= ADD STOCK =================
        elif action == "add_stock":

            medicine_id = request.form["medicine_id"]
            quantity = int(request.form["quantity"])

            cur.execute("""
                UPDATE medicines
                SET stock = stock + ?
                WHERE id = ?
            """, (quantity, medicine_id))

            conn.commit()
            message = "Stock added successfully."

    # reload medicines AFTER updates
    medicines = cur.execute("""
        SELECT *
        FROM medicines
        ORDER BY name ASC
    """).fetchall()

    conn.close()

    return render_template(
        "city/add_stock.html",
        medicines=medicines,
        message=message,
        username=session["username"]
    )
# ================= DELETE MEDICINE =================
@city_bp.route("/delete-medicine/<int:id>")
def delete_medicine(id):

    if session.get("role") != "city":
        return redirect(url_for("auth.login"))

    conn = get_db()
    cur = conn.cursor()

    # delete related barangay inventory first
    cur.execute("""
        DELETE FROM barangay_inventory
        WHERE medicine_id = ?
    """, (id,))

    # delete related requests
    cur.execute("""
        DELETE FROM requests
        WHERE medicine_id = ?
    """, (id,))

    # delete medicine
    cur.execute("""
        DELETE FROM medicines
        WHERE id = ?
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("city.add_stock"))