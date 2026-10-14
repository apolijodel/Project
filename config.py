import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"


def _secret_key():
    """Use SECRET_KEY from the environment, otherwise a key persisted in instance/."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key

    INSTANCE_DIR.mkdir(exist_ok=True)
    key_file = INSTANCE_DIR / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


class Config:
    SECRET_KEY = _secret_key()

    # Local MySQL / MariaDB (XAMPP defaults)
    DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "pasig_dasrs_db")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # Static files are served with a version query string, so they can be cached long.
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(days=365)

    # Demo accounts (created by setup_db.py). The login page lists them for one-click
    # sign-in; set SHOW_DEMO_ACCOUNTS=0 before showing the system to real users.
    SHOW_DEMO_ACCOUNTS = os.environ.get("SHOW_DEMO_ACCOUNTS", "1") == "1"
    DEMO_ACCOUNTS = [
        {"username": "admin", "password": "admin123", "role": "city",
         "label": "City Administrator", "scope": "Full access · approvals, stock, users"},
        {"username": "kapitolyo.bhw", "password": "brgy123", "role": "barangay",
         "barangay": "Kapitolyo", "label": "Barangay Kapitolyo", "scope": "Requests & inventory"},
        {"username": "ugong.bhw", "password": "brgy123", "role": "barangay",
         "barangay": "Ugong", "label": "Barangay Ugong", "scope": "Requests & inventory"},
        {"username": "bagongilog.bhw", "password": "brgy123", "role": "barangay",
         "barangay": "Bagong Ilog", "label": "Barangay Bagong Ilog", "scope": "Requests & inventory"},
    ]

    # Business rules
    LOW_STOCK_THRESHOLD = 20          # barangay stock at or below this is "low"
    MAX_REQUEST_QUANTITY = 10000
    FORECAST_HORIZON_WEEKS = 4        # default planning horizon
    FORECAST_LOOKBACK_WEEKS = 52
    SAFETY_STOCK_RATIO = 0.20         # keep 20% above forecast demand
    BARANGAY_COVER_WEEKS = 2          # barangay suggestions cover this many weeks of use
    PAGE_SIZE = 20
