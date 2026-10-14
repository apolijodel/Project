import math
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from db import query
from security import require_role, safe_next
from services.documents import requisition_slips
from services.forecast import barangay_suggestions, week_start
from services.inventory import (InventoryError, cancel_request, create_requests, dispense, parse_id,
                                parse_quantity)

barangay_bp = Blueprint("barangay", __name__, url_prefix="/barangay")
barangay_bp.before_request(require_role("barangay"))

STATUSES = ("pending", "approved", "released", "rejected", "cancelled")


def my_inventory():
    return query(
        """
        SELECT m.id, m.name, bi.stock, bi.updated_at
        FROM barangay_inventory bi JOIN medicines m ON m.id = bi.medicine_id
        WHERE bi.barangay_id = %s
        ORDER BY m.name
        """,
        (g.user["barangay_id"],),
    )


@barangay_bp.route("/")
def dashboard():
    bid = g.user["barangay_id"]
    threshold = current_app.config["LOW_STOCK_THRESHOLD"]
    inventory = my_inventory()
    low_stock = sorted((i for i in inventory if i["stock"] <= threshold), key=lambda i: i["stock"])

    status_counts = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) AS n FROM requests WHERE barangay_id = %s GROUP BY status", (bid,))}

    recent = query(
        """
        SELECT r.id, m.name AS medicine, r.quantity, r.status, r.requested_at, r.remarks
        FROM requests r JOIN medicines m ON m.id = r.medicine_id
        WHERE r.barangay_id = %s
        ORDER BY r.requested_at DESC, r.id DESC
        LIMIT 6
        """,
        (bid,),
    )

    # units received per week (last 12 complete weeks)
    current = week_start(date.today())
    start = current - timedelta(weeks=12)
    rows = query(
        """
        SELECT DATE(COALESCE(action_at, requested_at)) - INTERVAL WEEKDAY(COALESCE(action_at, requested_at)) DAY AS wk,
               SUM(quantity) AS qty
        FROM requests
        WHERE barangay_id = %s AND status = 'released'
          AND COALESCE(action_at, requested_at) >= %s AND COALESCE(action_at, requested_at) < %s
        GROUP BY wk
        """,
        (bid, start, current),
    )
    by_week = {r["wk"]: r["qty"] for r in rows}
    weeks = [start + timedelta(weeks=i) for i in range(12)]

    return render_template(
        "barangay/barangay_dashboard.html",
        suggestions=barangay_suggestions(bid),
        inventory=inventory,
        low_stock=low_stock,
        out_count=sum(1 for i in inventory if i["stock"] == 0),
        in_stock_units=sum(i["stock"] for i in inventory),
        status_counts=status_counts,
        recent=recent,
        chart_labels=[w.strftime("%b %d").replace(" 0", " ") for w in weeks],
        chart_values=[by_week.get(w, 0) for w in weeks],
    )


@barangay_bp.route("/request", methods=["GET", "POST"])
def request_supplies():
    bid = g.user["barangay_id"]

    suggestions = barangay_suggestions(bid)
    lines = []  # rows to show in the requisition form

    if request.method == "POST":
        raw = list(zip(request.form.getlist("medicine_id"), request.form.getlist("quantity")))
        lines = [(m, q) for m, q in raw if m or q]
        try:
            parsed = []
            for n, (m, q) in enumerate(lines, start=1):
                try:
                    parsed.append((parse_id(m, "medicine"), parse_quantity(q)))
                except InventoryError as e:
                    raise InventoryError(f"Item {n}: {e}" if len(lines) > 1 else str(e))
            names = create_requests(bid, parsed)
            what = f"{len(names)} medicines" if len(names) > 1 else names[0]
            flash(f"Request for {what} sent to the City Health Office.", "success")
            return redirect(url_for("barangay.request_supplies"))
        except InventoryError as e:
            flash(str(e), "error")
    elif request.args.get("suggested"):
        lines = [(str(sg["id"]), str(sg["suggested"])) for sg in suggestions]
    elif request.args.get("medicine", type=int):
        mid = request.args.get("medicine", type=int)
        hint = next((sg["suggested"] for sg in suggestions if sg["id"] == mid), "")
        lines = [(str(mid), str(hint))]

    medicines = query(
        """
        SELECT m.id, m.name, COALESCE(bi.stock, 0) AS stock,
               EXISTS(SELECT 1 FROM requests r WHERE r.barangay_id = %s AND r.medicine_id = m.id
                      AND r.status = 'pending') AS has_pending
        FROM medicines m
        LEFT JOIN barangay_inventory bi ON bi.medicine_id = m.id AND bi.barangay_id = %s
        WHERE m.is_active = 1
        ORDER BY m.name
        """,
        (bid, bid),
    )

    status = request.args.get("status", "all")
    if status not in STATUSES:
        status = "all"
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = current_app.config["PAGE_SIZE"]

    counts = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) AS n FROM requests WHERE barangay_id = %s GROUP BY status", (bid,))}
    counts["all"] = sum(counts.values())
    total = counts.get(status, 0)
    pages = max(math.ceil(total / per_page), 1)
    page = min(page, pages)

    where, args = "r.barangay_id = %s", [bid]
    if status != "all":
        where += " AND r.status = %s"
        args.append(status)
    history = query(
        f"""
        SELECT r.id, m.name AS medicine, r.quantity, r.status, r.source, r.requested_at,
               r.action_by, r.action_at, r.remarks
        FROM requests r JOIN medicines m ON m.id = r.medicine_id
        WHERE {where}
        ORDER BY r.requested_at DESC, r.id DESC
        LIMIT %s OFFSET %s
        """,
        (*args, per_page, (page - 1) * per_page),
    )

    return render_template(
        "barangay/request_supplies.html",
        medicines=medicines,
        suggestions=suggestions,
        lines=lines or [("", "")],
        cover_weeks=current_app.config["BARANGAY_COVER_WEEKS"],
        history=history,
        status=status,
        counts=counts,
        page=page,
        pages=pages,
        total=total,
    )


@barangay_bp.route("/request/<int:id>")
def request_detail(id):
    req = query(
        """
        SELECT r.*, m.name AS medicine, COALESCE(bi.stock, 0) AS my_stock
        FROM requests r
        JOIN medicines m ON m.id = r.medicine_id
        LEFT JOIN barangay_inventory bi ON bi.barangay_id = r.barangay_id AND bi.medicine_id = r.medicine_id
        WHERE r.id = %s AND r.barangay_id = %s
        """,
        (id, g.user["barangay_id"]),
        one=True,
    )
    if not req:
        abort(404)
    events = query(
        "SELECT status, actor, note, created_at FROM request_events WHERE request_id = %s ORDER BY created_at, id",
        (id,),
    )
    return render_template("barangay/request_detail.html", req=req, events=events)


@barangay_bp.route("/request/<int:id>/slip")
def slip(id):
    documents = requisition_slips([id], barangay_id=g.user["barangay_id"])
    if not documents:
        abort(404)
    return render_template("slip.html", slips=documents)


@barangay_bp.route("/request/<int:id>/cancel", methods=["POST"])
def cancel(id):
    try:
        cancel_request(id, g.user["barangay_id"])
        flash("Request cancelled.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return redirect(safe_next(request.form.get("next"), url_for("barangay.request_supplies")))


@barangay_bp.route("/inventory", methods=["GET", "POST"])
def inventory():
    bid = g.user["barangay_id"]

    if request.method == "POST":
        try:
            medicine_id = parse_id(request.form.get("medicine_id"), "medicine")
            quantity = parse_quantity(request.form.get("quantity"))
            dispense(bid, medicine_id, quantity, request.form.get("note"))
            flash(f"Recorded {quantity:,} units dispensed.", "success")
            return redirect(url_for("barangay.inventory"))
        except InventoryError as e:
            flash(str(e), "error")

    items = my_inventory()
    threshold = current_app.config["LOW_STOCK_THRESHOLD"]
    log = query(
        """
        SELECT sm.quantity, sm.note, sm.performed_by, sm.created_at, sm.movement, m.name AS medicine
        FROM stock_movements sm JOIN medicines m ON m.id = sm.medicine_id
        WHERE sm.barangay_id = %s
        ORDER BY sm.created_at DESC, sm.id DESC
        LIMIT 10
        """,
        (bid,),
    )
    return render_template(
        "barangay/barangay_inventory.html",
        inventory=items,
        low_count=sum(1 for i in items if 0 < i["stock"] <= threshold),
        out_count=sum(1 for i in items if i["stock"] == 0),
        total_units=sum(i["stock"] for i in items),
        log=log,
        form=request.form,
    )
