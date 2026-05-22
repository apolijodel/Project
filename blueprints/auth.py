from flask import Blueprint, render_template, request, redirect, url_for, session
import sqlite3

auth_bp = Blueprint("auth", __name__)


# ================= DB CONNECTION =================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


# ================= HOME =================
@auth_bp.route("/")
def home():
    return redirect(url_for("auth.login"))


# ================= LOGIN =================
@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()

        # get user
        user = cur.execute("""
            SELECT * FROM users
            WHERE username = ? AND password = ?
        """, (username, password)).fetchone()

        conn.close()

        if user:

            # ================= SESSION =================
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]

            # ONLY barangay users get barangay_id
            if user["role"] == "barangay":
                session["barangay_id"] = user["barangay_id"]

            # ================= REDIRECT =================
            if user["role"] == "city":
                return redirect(url_for("city.dashboard"))

            elif user["role"] == "barangay":
                return redirect(url_for("barangay.dashboard"))

        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")


# ================= LOGOUT =================
@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))