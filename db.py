"""MySQL access helpers (one connection per request, stored on flask.g)."""
from contextlib import contextmanager
from decimal import Decimal

import pymysql
from pymysql.cursors import DictCursor
from flask import current_app, g


def connect(config, database=True):
    return pymysql.connect(
        host=config["DB_HOST"],
        port=config["DB_PORT"],
        user=config["DB_USER"],
        password=config["DB_PASSWORD"],
        database=config["DB_NAME"] if database else None,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config)
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _clean(row):
    # MySQL returns SUM()/AVG() as Decimal, which is awkward in templates and JSON.
    for key, value in row.items():
        if isinstance(value, Decimal):
            row[key] = int(value) if value == value.to_integral_value() else float(value)
    return row


def query(sql, args=None, one=False):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        if one:
            row = cur.fetchone()
            return _clean(row) if row else None
        return [_clean(r) for r in cur.fetchall()]


def scalar(sql, args=None, default=0):
    row = query(sql, args, one=True)
    if not row:
        return default
    value = next(iter(row.values()))
    return default if value is None else value


def execute(sql, args=None):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


@contextmanager
def transaction():
    """Run several statements atomically; rolls back on any exception."""
    db = get_db()
    db.begin()
    try:
        with db.cursor() as cur:
            yield cur
        db.commit()
    except Exception:
        db.rollback()
        raise


def init_app(app):
    app.teardown_appcontext(close_db)
