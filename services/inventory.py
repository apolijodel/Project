"""
Stock and request workflow. Every operation that changes stock runs in a single
transaction and uses conditional UPDATEs, so stock can never go negative and a
request can never be processed twice (even with double clicks or two admins).
"""
import pymysql
from flask import current_app, g

from db import query, scalar, transaction


class InventoryError(Exception):
    """A business rule was violated; the message is safe to show to the user."""


def actor():
    return g.user["username"]


def parse_quantity(value, maximum=None):
    maximum = maximum or current_app.config["MAX_REQUEST_QUANTITY"]
    try:
        qty = int(str(value).strip())
    except (TypeError, ValueError):
        raise InventoryError("Enter the quantity as a whole number.")
    if qty < 1 or qty > maximum:
        raise InventoryError(f"Quantity must be between 1 and {maximum:,}.")
    return qty


def parse_id(value, label="item"):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InventoryError(f"Please choose a {label}.")


def _event(cur, request_id, status, note=None):
    """Record a status change in the request timeline (same transaction as the change)."""
    cur.execute(
        "INSERT INTO request_events (request_id, status, actor, note) VALUES (%s, %s, %s, %s)",
        (request_id, status, actor(), note),
    )


def _active_medicine(medicine_id):
    med = query("SELECT id, name, stock, is_active FROM medicines WHERE id = %s", (medicine_id,), one=True)
    if not med or not med["is_active"]:
        raise InventoryError("That medicine is not available.")
    return med


# ----------------------------------------------------------------- requests

def create_requests(barangay_id, lines):
    """
    Submit a requisition: several (medicine_id, quantity) lines at once.
    All lines are validated first; either every line is saved or none is.
    """
    if not lines:
        raise InventoryError("Add at least one medicine to the request.")
    ids = [m for m, _ in lines]
    if len(set(ids)) != len(ids):
        raise InventoryError("Each medicine can only appear once in a request.")

    meds = {m["id"]: m for m in query(
        "SELECT id, name, is_active FROM medicines WHERE id IN %s", (tuple(ids),))}
    for medicine_id in ids:
        if medicine_id not in meds:
            raise InventoryError("That medicine is not available.")
        if not meds[medicine_id]["is_active"]:
            raise InventoryError(f"{meds[medicine_id]['name']} is no longer available.")

    pending = [meds[r["medicine_id"]]["name"] for r in query(
        "SELECT DISTINCT medicine_id FROM requests "
        "WHERE barangay_id = %s AND status = 'pending' AND medicine_id IN %s",
        (barangay_id, tuple(ids)),
    )]
    if pending:
        raise InventoryError(
            "You already have a pending request for " + ", ".join(sorted(pending)) +
            ". Cancel it first, or wait for the City Health Office to act on it."
        )

    with transaction() as cur:
        for medicine_id, quantity in lines:
            cur.execute(
                "INSERT INTO requests (barangay_id, medicine_id, quantity, status, source, requested_at) "
                "VALUES (%s, %s, %s, 'pending', 'request', NOW())",
                (barangay_id, medicine_id, quantity),
            )
            _event(cur, cur.lastrowid, "pending")
    return [meds[m]["name"] for m in ids]


def cancel_request(request_id, barangay_id):
    with transaction() as cur:
        changed = cur.execute(
            "UPDATE requests SET status = 'cancelled', action_by = %s, action_at = NOW() "
            "WHERE id = %s AND barangay_id = %s AND status = 'pending'",
            (actor(), request_id, barangay_id),
        )
        if changed:
            _event(cur, request_id, "cancelled")
    if not changed:
        raise InventoryError("Only your own pending requests can be cancelled.")


def approve_request(request_id):
    with transaction() as cur:
        changed = cur.execute(
            "UPDATE requests SET status = 'approved', action_by = %s, action_at = NOW() "
            "WHERE id = %s AND status = 'pending'",
            (actor(), request_id),
        )
        if changed:
            _event(cur, request_id, "approved")
    if not changed:
        raise InventoryError("This request is no longer pending.")


def reject_request(request_id, remarks):
    remarks = (remarks or "").strip()[:255] or None
    with transaction() as cur:
        changed = cur.execute(
            "UPDATE requests SET status = 'rejected', action_by = %s, action_at = NOW(), remarks = %s "
            "WHERE id = %s AND status IN ('pending', 'approved')",
            (actor(), remarks, request_id),
        )
        if changed:
            _event(cur, request_id, "rejected", remarks)
    if not changed:
        raise InventoryError("Only pending or approved requests can be rejected.")


def _move_to_barangay(cur, barangay_id, medicine_id, quantity, request_id, note=None):
    moved = cur.execute(
        "UPDATE medicines SET stock = stock - %s WHERE id = %s AND stock >= %s",
        (quantity, medicine_id, quantity),
    )
    if not moved:
        cur.execute("SELECT name, stock FROM medicines WHERE id = %s", (medicine_id,))
        med = cur.fetchone()
        raise InventoryError(
            f"Not enough city stock of {med['name']}: {med['stock']:,} available, {quantity:,} needed."
        )
    cur.execute(
        "INSERT INTO barangay_inventory (barangay_id, medicine_id, stock) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE stock = stock + VALUES(stock)",
        (barangay_id, medicine_id, quantity),
    )
    cur.execute(
        "INSERT INTO stock_movements (medicine_id, barangay_id, request_id, movement, quantity, performed_by, note) "
        "VALUES (%s, %s, %s, 'release', %s, %s, %s)",
        (medicine_id, barangay_id, request_id, quantity, actor(), note),
    )


def release_request(request_id):
    with transaction() as cur:
        cur.execute("SELECT * FROM requests WHERE id = %s FOR UPDATE", (request_id,))
        req = cur.fetchone()
        if not req:
            raise InventoryError("Request not found.")
        if req["status"] != "approved":
            raise InventoryError("Only approved requests can be released.")
        _move_to_barangay(cur, req["barangay_id"], req["medicine_id"], req["quantity"], request_id)
        cur.execute(
            "UPDATE requests SET status = 'released', action_by = %s, action_at = NOW() WHERE id = %s",
            (actor(), request_id),
        )
        _event(cur, request_id, "released")


def distribute(barangay_id, medicine_id, quantity):
    """City-initiated distribution: recorded as a released request with source 'direct'."""
    med = _active_medicine(medicine_id)
    if not scalar("SELECT COUNT(*) FROM barangays WHERE id = %s", (barangay_id,)):
        raise InventoryError("Please choose a barangay.")
    with transaction() as cur:
        cur.execute(
            "INSERT INTO requests (barangay_id, medicine_id, quantity, status, source, requested_at, "
            "action_by, action_at, remarks) "
            "VALUES (%s, %s, %s, 'released', 'direct', NOW(), %s, NOW(), 'Direct distribution')",
            (barangay_id, medicine_id, quantity, actor()),
        )
        request_id = cur.lastrowid
        _move_to_barangay(cur, barangay_id, medicine_id, quantity, request_id, "Direct distribution")
        _event(cur, request_id, "released", "Direct distribution")
    return med


# ----------------------------------------------------------------- city stock

def add_medicine(name, initial_stock=0):
    name = " ".join((name or "").split())
    if len(name) < 2 or len(name) > 150:
        raise InventoryError("Enter a medicine name (2–150 characters).")
    try:
        with transaction() as cur:
            cur.execute("INSERT INTO medicines (name, stock) VALUES (%s, %s)", (name, initial_stock))
            if initial_stock:
                cur.execute(
                    "INSERT INTO stock_movements (medicine_id, movement, quantity, performed_by, note) "
                    "VALUES (%s, 'restock', %s, %s, 'Initial stock')",
                    (cur.lastrowid, initial_stock, actor()),
                )
    except pymysql.err.IntegrityError:
        raise InventoryError(f"“{name}” already exists.")
    return name


def restock(medicine_id, quantity, note=None):
    med = _active_medicine(medicine_id)
    with transaction() as cur:
        cur.execute("UPDATE medicines SET stock = stock + %s WHERE id = %s", (quantity, medicine_id))
        cur.execute(
            "INSERT INTO stock_movements (medicine_id, movement, quantity, performed_by, note) "
            "VALUES (%s, 'restock', %s, %s, %s)",
            (medicine_id, quantity, actor(), (note or "").strip()[:255] or None),
        )
    return med


def set_medicine_active(medicine_id, active):
    with transaction() as cur:
        changed = cur.execute("UPDATE medicines SET is_active = %s WHERE id = %s", (1 if active else 0, medicine_id))
        if active is False:
            # open requests for an archived medicine can no longer be fulfilled
            cur.execute(
                "INSERT INTO request_events (request_id, status, actor, note) "
                "SELECT id, 'rejected', %s, 'Medicine archived' FROM requests "
                "WHERE medicine_id = %s AND status IN ('pending', 'approved')",
                (actor(), medicine_id),
            )
            cur.execute(
                "UPDATE requests SET status = 'rejected', action_by = %s, action_at = NOW(), "
                "remarks = 'Medicine archived' WHERE medicine_id = %s AND status IN ('pending', 'approved')",
                (actor(), medicine_id),
            )
    if not changed:
        raise InventoryError("Medicine not found or already in that state.")


# ----------------------------------------------------------------- barangay stock

def dispense(barangay_id, medicine_id, quantity, note=None):
    with transaction() as cur:
        changed = cur.execute(
            "UPDATE barangay_inventory SET stock = stock - %s "
            "WHERE barangay_id = %s AND medicine_id = %s AND stock >= %s",
            (quantity, barangay_id, medicine_id, quantity),
        )
        if not changed:
            cur.execute(
                "SELECT stock FROM barangay_inventory WHERE barangay_id = %s AND medicine_id = %s",
                (barangay_id, medicine_id),
            )
            row = cur.fetchone()
            available = row["stock"] if row else 0
            raise InventoryError(f"Not enough stock: {available:,} available, {quantity:,} requested.")
        cur.execute(
            "INSERT INTO stock_movements (medicine_id, barangay_id, movement, quantity, performed_by, note) "
            "VALUES (%s, %s, 'dispense', %s, %s, %s)",
            (medicine_id, barangay_id, quantity, actor(), (note or "").strip()[:255] or None),
        )


def bulk(action, request_ids, remarks=None):
    """Apply one workflow action to many requests. Returns (done_count, [error messages])."""
    handlers = {
        "approve": approve_request,
        "reject": lambda rid: reject_request(rid, remarks),
        "release": release_request,
    }
    if action not in handlers:
        raise InventoryError("Unknown action.")
    if action == "release":
        # first come, first served when stock is limited
        request_ids = [r["id"] for r in query(
            "SELECT id FROM requests WHERE id IN %s ORDER BY requested_at, id", (tuple(request_ids),))]
    done, errors = 0, []
    for rid in request_ids:
        try:
            handlers[action](rid)
            done += 1
        except InventoryError as e:
            errors.append(f"#{rid}: {e}")
    return done, errors
