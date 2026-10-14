"""
Notification feed built from request_events.

City users hear about new and cancelled barangay requests; barangay users hear when
their requests are approved, released or rejected (and about direct distributions).
"""
from datetime import datetime, timedelta

from db import execute, query, scalar

FIRST_VISIT_WINDOW = timedelta(days=7)  # before a user has ever opened the feed


def _scope(user):
    if user["role"] == "city":
        return "e.status IN ('pending', 'cancelled') AND r.source = 'request'", ()
    return "e.status IN ('approved', 'released', 'rejected') AND r.barangay_id = %s", (user["barangay_id"],)


def _since(user):
    return user.get("notifications_seen_at") or datetime.now() - FIRST_VISIT_WINDOW


def unread_count(user):
    where, args = _scope(user)
    return scalar(
        f"""
        SELECT COUNT(*) FROM request_events e JOIN requests r ON r.id = e.request_id
        WHERE {where} AND e.created_at > %s
        """,
        (*args, _since(user)),
    )


def feed(user, limit=8):
    where, args = _scope(user)
    rows = query(
        f"""
        SELECT e.id, e.status, e.actor, e.note, e.created_at,
               r.id AS request_id, r.quantity, r.source, m.name AS medicine, b.name AS barangay
        FROM request_events e
        JOIN requests r ON r.id = e.request_id
        JOIN medicines m ON m.id = r.medicine_id
        JOIN barangays b ON b.id = r.barangay_id
        WHERE {where}
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT %s
        """,
        (*args, limit),
    )
    since = _since(user)
    for r in rows:
        r["unread"] = r["created_at"] > since
    return rows


def mark_seen(user_id):
    execute("UPDATE users SET notifications_seen_at = NOW() WHERE id = %s", (user_id,))
