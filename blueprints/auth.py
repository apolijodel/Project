from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from db import execute, query
from security import login_user, safe_next

auth_bp = Blueprint("auth", __name__)


def home_for(user):
    return url_for("city.dashboard") if user["role"] == "city" else url_for("barangay.dashboard")


@auth_bp.route("/")
def home():
    if g.user:
        return redirect(home_for(g.user))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user and request.method == "GET":
        return redirect(home_for(g.user))

    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = query(
            "SELECT id, username, password_hash, role, barangay_id FROM users WHERE username = %s",
            (username,),
            one=True,
        )
        if user and check_password_hash(user["password_hash"], password):
            if user["role"] == "barangay" and not user["barangay_id"]:
                flash("This account is not linked to a barangay. Please contact the city administrator.", "error")
            else:
                login_user(user)
                execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
                return redirect(safe_next(request.args.get("next"), home_for(user)))
        else:
            flash("Incorrect username or password.", "error")

    return render_template("login.html", username=username, demo_accounts=demo_accounts())


def demo_accounts():
    """Demo accounts from config that actually exist in the database."""
    accounts = current_app.config["DEMO_ACCOUNTS"]
    if not current_app.config["SHOW_DEMO_ACCOUNTS"] or not accounts:
        return []
    existing = {r["username"].lower() for r in query(
        "SELECT username FROM users WHERE username IN %s", (tuple(a["username"] for a in accounts),))}
    return [a for a in accounts if a["username"].lower() in existing]


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
