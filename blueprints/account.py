from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import execute, query
from services import notifications

account_bp = Blueprint("account", __name__, url_prefix="/account")


@account_bp.before_request
def require_login():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


@account_bp.route("/notifications")
def notification_feed():
    """HTML fragment for the notification dropdown."""
    return render_template("_notifications.html", items=notifications.feed(g.user))


@account_bp.route("/notifications/seen", methods=["POST"])
def notifications_seen():
    notifications.mark_seen(g.user["id"])
    return "", 204


@account_bp.route("/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        row = query("SELECT password_hash FROM users WHERE id = %s", (g.user["id"],), one=True)

        if not check_password_hash(row["password_hash"], current):
            flash("Your current password is incorrect.", "error")
        elif len(new) < 8:
            flash("Choose a new password with at least 8 characters.", "error")
        elif new != confirm:
            flash("The new passwords don't match.", "error")
        elif new == current:
            flash("The new password must be different from the current one.", "error")
        else:
            execute("UPDATE users SET password_hash = %s WHERE id = %s",
                    (generate_password_hash(new), g.user["id"]))
            flash("Password changed. Use it the next time you sign in.", "success")
            return redirect(url_for("auth.home"))

    return render_template("account/password.html")
