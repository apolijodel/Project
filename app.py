import logging
import os
from datetime import date, datetime

import pymysql
from flask import Flask, flash, g, redirect, render_template, request, url_for

import db
import migrations
import security
from blueprints.account import account_bp
from blueprints.auth import auth_bp
from blueprints.barangay import barangay_bp
from blueprints.city import city_bp
from config import Config
from services import notifications
from services.forecast import warm_cache


def create_app(config=Config):
    app = Flask(__name__)
    app.config.from_object(config)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    run_migrations(app)
    security.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(city_bp)
    app.register_blueprint(barangay_bp)

    register_template_helpers(app)
    register_error_handlers(app)

    warmed = {"done": False}

    @app.before_request
    def start_forecast_warmup():
        if not warmed["done"]:
            warmed["done"] = True
            warm_cache(app)

    return app


def run_migrations(app):
    """Upgrade older databases in place; skipped quietly if MySQL is not reachable yet."""
    try:
        conn = db.connect(app.config)
    except pymysql.err.OperationalError as e:
        app.logger.warning("Schema check skipped, database unavailable: %s", e)
        return
    try:
        for change in migrations.upgrade(conn):
            app.logger.info("Schema upgraded: %s", change)
    finally:
        conn.close()


def register_template_helpers(app):
    static_dir = app.static_folder

    @app.template_global()
    def asset(filename):
        """Static URL with a version stamp, so browsers can cache files long-term."""
        try:
            version = int(os.path.getmtime(os.path.join(static_dir, filename)))
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)

    @app.template_global()
    def page_url(page):
        args = request.args.to_dict()
        args["page"] = page
        return url_for(request.endpoint, **(request.view_args or {}), **args)

    @app.template_filter("dt")
    def format_datetime(value, with_time=True):
        if not value:
            return "—"
        if isinstance(value, datetime):
            if with_time and (value.hour or value.minute):
                return value.strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ")
            value = value.date()
        if isinstance(value, date):
            return value.strftime("%b %d, %Y").replace(" 0", " ")
        return str(value)

    @app.template_filter("ago")
    def time_ago(value):
        if not value:
            return "—"
        if not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        seconds = (datetime.now() - value).total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)} min ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)} hr ago"
        days = int(seconds // 86400)
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days} days ago"
        return format_datetime(value, with_time=False)

    @app.template_filter("num")
    def format_number(value, decimals=0):
        if value is None:
            return "—"
        return f"{value:,.{decimals}f}"

    @app.context_processor
    def inject_globals():
        nav_counts = {}
        user = getattr(g, "user", None)
        if user and user["role"] == "city":
            nav_counts["requests"] = db.scalar("SELECT COUNT(*) FROM requests WHERE status = 'pending'")
            nav_counts["release"] = db.scalar("SELECT COUNT(*) FROM requests WHERE status = 'approved'")
        if user:
            nav_counts["notifications"] = notifications.unread_count(user)
        return {
            "current_user": user,
            "nav_counts": nav_counts,
            "today": date.today(),
            "low_threshold": app.config["LOW_STOCK_THRESHOLD"],
        }


def register_error_handlers(app):
    def render_error(code, title, message):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(e):
        if getattr(e, "description", "") == "csrf":
            flash("Your session expired. Please try again.", "error")
            return redirect(request.referrer or url_for("auth.login"))
        return render_error(400, "Bad request", "The form could not be processed. Please try again.")

    @app.errorhandler(403)
    def forbidden(e):
        return render_error(403, "Access denied", "Your account does not have access to this page.")

    @app.errorhandler(404)
    def not_found(e):
        return render_error(404, "Page not found", "The page or record you are looking for does not exist.")

    @app.errorhandler(pymysql.err.OperationalError)
    def database_down(e):
        app.logger.error("Database error: %s", e)
        return render_error(
            503,
            "Database unavailable",
            "Cannot connect to the MySQL database. Start MySQL in the XAMPP Control Panel, "
            "then run “python setup_db.py” if this is the first time.",
        )

    @app.errorhandler(500)
    def server_error(e):
        return render_error(500, "Something went wrong", "An unexpected error occurred. Please try again.")


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
