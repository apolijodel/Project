"""Session login, role guards and CSRF protection."""
import secrets
from urllib.parse import urlparse

from flask import abort, flash, g, redirect, request, session, url_for

from db import query


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def login_user(user):
    session.clear()  # new session id, prevents session fixation
    session.permanent = True
    session["user_id"] = user["id"]
    csrf_token()


def safe_next(target, fallback):
    """Only allow redirects back into this site."""
    if target:
        parts = urlparse(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return fallback


def require_role(role):
    """Blueprint before_request hook: the user must be logged in with this role."""
    def guard():
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if g.user["role"] != role:
            abort(403)
        if role == "barangay" and not g.user["barangay_id"]:
            session.clear()
            flash("Your account is not linked to a barangay. Please contact the city administrator.", "error")
            return redirect(url_for("auth.login"))
    return guard


def init_app(app):
    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint == "static":
            return
        user_id = session.get("user_id")
        if user_id:
            g.user = query(
                """
                SELECT u.id, u.username, u.role, u.barangay_id, u.notifications_seen_at, b.name AS barangay_name
                FROM users u LEFT JOIN barangays b ON b.id = u.barangay_id
                WHERE u.id = %s
                """,
                (user_id,),
                one=True,
            )
            if g.user is None:  # account was deleted
                session.clear()

    @app.before_request
    def check_csrf():
        if request.method != "POST":
            return
        expected = session.get("_csrf")
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, sent):
            abort(400, description="csrf")

    app.jinja_env.globals["csrf_token"] = csrf_token
