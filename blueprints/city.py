import csv
import io
import math
from datetime import date, datetime, timedelta

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)
from werkzeug.security import generate_password_hash

import pymysql
from db import query, scalar, transaction
from security import require_role, safe_next
from services import forecast as fc
from services.documents import requisition_slips
from services.inventory import (InventoryError, add_medicine, approve_request, bulk, distribute, parse_id,
                                parse_quantity, reject_request, release_request, restock,
                                set_medicine_active)

city_bp = Blueprint("city", __name__, url_prefix="/city")
city_bp.before_request(require_role("city"))

STATUSES = ("pending", "approved", "released", "rejected", "cancelled")
HORIZON_CHOICES = (1, 2, 4, 8, 12)


def back(default_endpoint, **kwargs):
    return redirect(safe_next(request.form.get("next"), url_for(default_endpoint, **kwargs)))


def weekly_totals(weeks, where="", args=()):
    """Units per week for the last `weeks` complete weeks (zero-filled), for trend charts."""
    current = fc.week_start(date.today())
    start = current - timedelta(weeks=weeks)
    rows = query(
        f"""
        SELECT DATE(requested_at) - INTERVAL WEEKDAY(requested_at) DAY AS wk, SUM(quantity) AS qty
        FROM requests
        WHERE requested_at >= %s AND requested_at < %s {where}
        GROUP BY wk
        """,
        (start, current, *args),
    )
    by_week = {r["wk"]: r["qty"] for r in rows}
    labels = [start + timedelta(weeks=i) for i in range(weeks)]
    return [w.strftime("%b %d").replace(" 0", " ") for w in labels], [by_week.get(w, 0) for w in labels]


# ----------------------------------------------------------------- dashboard

@city_bp.route("/")
def dashboard():
    stats = query(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(status = 'pending'), 0) AS pending,
               COALESCE(SUM(status = 'approved'), 0) AS approved,
               COALESCE(SUM(CASE WHEN status = 'released'
                    AND COALESCE(action_at, requested_at) >= NOW() - INTERVAL 30 DAY
                    THEN quantity END), 0) AS released_30d
        FROM requests
        """,
        one=True,
    )
    status_counts = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) AS n FROM requests GROUP BY status")}

    outlook = fc.medicine_outlook()
    at_risk = [m for m in outlook if m["risk"] in ("critical", "low")]

    trend_labels, trend_values = weekly_totals(12, "AND status IN %s", (fc.DEMAND_STATUSES,))

    pending = query(
        """
        SELECT r.id, r.quantity, r.requested_at, b.name AS barangay, m.name AS medicine, m.stock
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.status = 'pending'
        ORDER BY r.requested_at ASC
        LIMIT 6
        """
    )
    recent = query(
        """
        SELECT r.id, r.quantity, r.source, COALESCE(r.action_at, r.requested_at) AS released_at,
               b.name AS barangay, m.name AS medicine
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.status = 'released'
        ORDER BY released_at DESC, r.id DESC
        LIMIT 8
        """
    )

    return render_template(
        "city/city_dashboard.html",
        stats=stats,
        status_counts=status_counts,
        barangay_count=scalar("SELECT COUNT(*) FROM barangays"),
        medicine_count=len(outlook),
        at_risk=at_risk,
        has_demand=any(m["risk"] != "idle" for m in outlook),
        critical_count=sum(1 for m in at_risk if m["risk"] == "critical"),
        trend_labels=trend_labels,
        trend_values=trend_values,
        pending=pending,
        recent=recent,
        horizon=current_app.config["FORECAST_HORIZON_WEEKS"],
    )


# ----------------------------------------------------------------- barangays

@city_bp.route("/barangays")
def all_barangays():
    barangays = query(
        """
        SELECT b.id, b.name,
               COALESCE(r.total_requests, 0) AS total_requests,
               COALESCE(r.pending, 0) AS pending,
               COALESCE(r.received, 0) AS received,
               r.last_request,
               COALESCE(i.item_count, 0) AS item_count,
               COALESCE(i.low, 0) AS low,
               COALESCE(i.out_of_stock, 0) AS out_of_stock,
               (SELECT COUNT(*) FROM users u WHERE u.barangay_id = b.id) AS accounts
        FROM barangays b
        LEFT JOIN (
            SELECT barangay_id, COUNT(*) AS total_requests,
                   SUM(status = 'pending') AS pending,
                   SUM(CASE WHEN status = 'released' THEN quantity ELSE 0 END) AS received,
                   MAX(requested_at) AS last_request
            FROM requests GROUP BY barangay_id
        ) r ON r.barangay_id = b.id
        LEFT JOIN (
            SELECT barangay_id, COUNT(*) AS item_count,
                   SUM(stock > 0 AND stock <= %s) AS low,
                   SUM(stock = 0) AS out_of_stock
            FROM barangay_inventory GROUP BY barangay_id
        ) i ON i.barangay_id = b.id
        ORDER BY b.name
        """,
        (current_app.config["LOW_STOCK_THRESHOLD"],),
    )
    for b in barangays:
        if b["out_of_stock"]:
            b["health"] = "critical"
        elif b["low"]:
            b["health"] = "low"
        elif b["item_count"]:
            b["health"] = "adequate"
        else:
            b["health"] = "idle"
        b["healthy_pct"] = (round(100 * (b["item_count"] - b["low"] - b["out_of_stock"]) / b["item_count"])
                            if b["item_count"] else 0)

    return render_template(
        "city/all_barangays.html",
        barangays=barangays,
        totals={
            "requests": sum(b["total_requests"] for b in barangays),
            "received": sum(b["received"] for b in barangays),
            "attention": sum(1 for b in barangays if b["health"] in ("critical", "low") or b["pending"]),
        },
    )


@city_bp.route("/barangay/<int:id>")
def barangay_detail(id):
    barangay = query("SELECT * FROM barangays WHERE id = %s", (id,), one=True)
    if not barangay:
        abort(404)

    threshold = current_app.config["LOW_STOCK_THRESHOLD"]
    inventory = query(
        """
        SELECT m.id, m.name, bi.stock, bi.updated_at
        FROM barangay_inventory bi JOIN medicines m ON m.id = bi.medicine_id
        WHERE bi.barangay_id = %s
        ORDER BY bi.stock ASC, m.name
        """,
        (id,),
    )
    stats = query(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(status = 'pending'), 0) AS pending,
               COALESCE(SUM(CASE WHEN status = 'released' THEN quantity END), 0) AS received
        FROM requests WHERE barangay_id = %s
        """,
        (id,),
        one=True,
    )
    demand = query(
        """
        SELECT m.name, SUM(r.quantity) AS units
        FROM requests r JOIN medicines m ON m.id = r.medicine_id
        WHERE r.barangay_id = %s AND r.status IN %s
        GROUP BY m.id, m.name
        ORDER BY units DESC
        LIMIT 10
        """,
        (id, fc.DEMAND_STATUSES),
    )
    history = query(
        """
        SELECT r.id, m.name AS medicine, r.quantity, r.status, r.source, r.requested_at, r.action_by
        FROM requests r JOIN medicines m ON m.id = r.medicine_id
        WHERE r.barangay_id = %s
        ORDER BY r.requested_at DESC, r.id DESC
        LIMIT 15
        """,
        (id,),
    )
    trend_labels, trend_values = weekly_totals(
        12, "AND status IN %s AND barangay_id = %s", (fc.DEMAND_STATUSES, id))

    return render_template(
        "city/barangay_detail.html",
        barangay=barangay,
        inventory=inventory,
        low_count=sum(1 for i in inventory if i["stock"] <= threshold),
        stats=stats,
        demand=demand,
        history=history,
        accounts=query("SELECT username, last_login_at FROM users WHERE barangay_id = %s", (id,)),
        trend_labels=trend_labels,
        trend_values=trend_values,
    )


# ----------------------------------------------------------------- requests

@city_bp.route("/requests")
def view_requests():
    status = request.args.get("status", "pending")
    if status not in STATUSES and status != "all":
        status = "pending"
    search = (request.args.get("q") or "").strip()
    barangay_id = request.args.get("barangay", type=int)
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = current_app.config["PAGE_SIZE"]

    filters, args = [], []
    if search:
        filters.append("(b.name LIKE %s OR m.name LIKE %s)")
        args += [f"%{search}%", f"%{search}%"]
    if barangay_id:
        filters.append("r.barangay_id = %s")
        args.append(barangay_id)
    base_where = " AND ".join(filters) or "1=1"

    counts = {r["status"]: r["n"] for r in query(
        f"""
        SELECT r.status, COUNT(*) AS n
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE {base_where}
        GROUP BY r.status
        """,
        args,
    )}
    counts["all"] = sum(counts.values())

    where, list_args = base_where, list(args)
    if status != "all":
        where += " AND r.status = %s"
        list_args.append(status)

    total = counts.get(status, 0)
    pages = max(math.ceil(total / per_page), 1)
    page = min(page, pages)
    # oldest first for the work queues, newest first for history
    order = "r.requested_at ASC, r.id ASC" if status in ("pending", "approved") else "r.requested_at DESC, r.id DESC"

    rows = query(
        f"""
        SELECT r.id, r.quantity, r.status, r.source, r.requested_at, r.action_by, r.action_at, r.remarks,
               b.id AS barangay_id, b.name AS barangay, m.name AS medicine, m.stock
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE {where}
        ORDER BY {order}
        LIMIT %s OFFSET %s
        """,
        (*list_args, per_page, (page - 1) * per_page),
    )

    return render_template(
        "city/requests.html",
        requests=rows,
        status=status,
        counts=counts,
        search=search,
        barangay_id=barangay_id,
        barangays=query("SELECT id, name FROM barangays ORDER BY name"),
        page=page,
        pages=pages,
        total=total,
    )


@city_bp.route("/request/<int:id>")
def request_details(id):
    req = query(
        """
        SELECT r.*, b.name AS barangay, m.name AS medicine, m.stock AS city_stock, m.is_active,
               COALESCE(bi.stock, 0) AS barangay_stock
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        LEFT JOIN barangay_inventory bi ON bi.barangay_id = r.barangay_id AND bi.medicine_id = r.medicine_id
        WHERE r.id = %s
        """,
        (id,),
        one=True,
    )
    if not req:
        abort(404)

    horizon = current_app.config["FORECAST_HORIZON_WEEKS"]
    forecast = fc.get_forecast(req["medicine_id"])
    outlook = fc.assess(req["city_stock"], forecast, horizon)

    open_request = req["status"] in ("pending", "approved")
    remaining = req["city_stock"] - req["quantity"]
    if not open_request:
        verdict = None
    elif remaining < 0:
        verdict = ("critical", "Insufficient city stock",
                   f"Only {req['city_stock']:,} units are available but {req['quantity']:,} were requested. "
                   f"Restock at least {-remaining:,} units before releasing.")
    elif remaining < outlook["target"] and outlook["demand"] > 0:
        verdict = ("low", "Approve with caution",
                   f"Releasing leaves {remaining:,} units, below the {outlook['target']:,}-unit safety level "
                   f"for the next {horizon} weeks of forecast demand. Plan a restock of about "
                   f"{outlook['target'] - remaining:,} units.")
    else:
        cover = f" (about {remaining / outlook['weekly']:.1f} weeks of forecast demand)" if outlook["weekly"] else ""
        verdict = ("adequate", "Safe to release",
                   f"After release, {remaining:,} units remain in city stock{cover}.")

    history_weeks = 26
    chart = None
    if forecast:
        hist_dates = forecast.weeks[-history_weeks:]
        chart = {
            "labels": [d.strftime("%b %d").replace(" 0", " ") for d in hist_dates + forecast.future_weeks[:horizon]],
            "history": [round(v, 1) for v in forecast.history[-history_weeks:]],
            "forecast": [round(v, 1) for v in forecast.future[:horizon]],
        }

    events = query(
        "SELECT status, actor, note, created_at FROM request_events WHERE request_id = %s ORDER BY created_at, id",
        (id,),
    )

    related = query(
        """
        SELECT id, quantity, status, requested_at FROM requests
        WHERE barangay_id = %s AND medicine_id = %s AND id <> %s
        ORDER BY requested_at DESC LIMIT 5
        """,
        (req["barangay_id"], req["medicine_id"], id),
    )

    return render_template(
        "city/request_details.html",
        req=req,
        outlook=outlook,
        verdict=verdict,
        chart=chart,
        horizon=horizon,
        related=related,
        events=events,
    )


@city_bp.route("/request/<int:id>/approve", methods=["POST"])
def approve(id):
    try:
        approve_request(id)
        flash(f"Request #{id} approved. It is now ready for release.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return back("city.request_details", id=id)


@city_bp.route("/request/<int:id>/reject", methods=["POST"])
def reject(id):
    try:
        reject_request(id, request.form.get("remarks"))
        flash(f"Request #{id} rejected.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return back("city.request_details", id=id)


@city_bp.route("/request/<int:id>/release", methods=["POST"])
def release(id):
    try:
        release_request(id)
        flash(f"Request #{id} released. Stock moved to the barangay inventory.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return back("city.request_details", id=id)


def _selected_ids():
    ids = []
    for raw in request.values.getlist("ids") + (request.values.get("ids") or "").split(","):
        raw = raw.strip()
        if raw.isdigit() and int(raw) not in ids:
            ids.append(int(raw))
    return ids[:200]


@city_bp.route("/requests/bulk", methods=["POST"])
def bulk_requests():
    ids = _selected_ids()
    action = request.form.get("action")
    if not ids:
        flash("Select at least one request first.", "error")
        return back("city.view_requests")
    try:
        done, errors = bulk(action, ids, request.form.get("remarks"))
    except InventoryError as e:
        flash(str(e), "error")
        return back("city.view_requests")

    verb = {"approve": "approved", "reject": "rejected", "release": "released"}[action]
    if done:
        flash(f"{done} request{'s' if done != 1 else ''} {verb}.", "success")
    if errors:
        more = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
        flash(f"{len(errors)} could not be {verb}: " + "; ".join(errors[:3]) + more, "error")
    return back("city.view_requests")


@city_bp.route("/slips")
def slips():
    """Printable Requisition and Issue Slips (one per barangay) for ?ids=1,2,3."""
    documents = requisition_slips(_selected_ids())
    if not documents:
        abort(404)
    return render_template("slip.html", slips=documents)


# ----------------------------------------------------------------- activity log

MOVEMENTS = ("restock", "release", "dispense")


def _activity_filters():
    f = {
        "type": request.args.get("type", ""),
        "barangay": request.args.get("barangay", type=int),
        "q": (request.args.get("q") or "").strip(),
        "start": request.args.get("start", ""),
        "end": request.args.get("end", ""),
    }
    where, args = ["1=1"], []
    if f["type"] in MOVEMENTS:
        where.append("sm.movement = %s")
        args.append(f["type"])
    else:
        f["type"] = ""
    if f["barangay"]:
        where.append("sm.barangay_id = %s")
        args.append(f["barangay"])
    if f["q"]:
        where.append("(m.name LIKE %s OR sm.note LIKE %s OR sm.performed_by LIKE %s)")
        args += [f"%{f['q']}%"] * 3
    for key, op in (("start", ">="), ("end", "<")):
        try:
            day = datetime.strptime(f[key], "%Y-%m-%d").date()
        except ValueError:
            f[key] = ""
            continue
        where.append(f"sm.created_at {op} %s")
        args.append(day if key == "start" else day + timedelta(days=1))
    return f, " AND ".join(where), args


ACTIVITY_FROM = """
    FROM stock_movements sm
    JOIN medicines m ON m.id = sm.medicine_id
    LEFT JOIN barangays b ON b.id = sm.barangay_id
"""


@city_bp.route("/activity")
def activity():
    filters, where, args = _activity_filters()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 25

    totals = {r["movement"]: r for r in query(
        f"SELECT sm.movement, COUNT(*) AS n, SUM(sm.quantity) AS units {ACTIVITY_FROM} WHERE {where} GROUP BY sm.movement",
        args,
    )}
    total = sum(t["n"] for t in totals.values())
    pages = max(math.ceil(total / per_page), 1)
    page = min(page, pages)
    rows = query(
        f"""
        SELECT sm.id, sm.movement, sm.quantity, sm.performed_by, sm.note, sm.created_at, sm.request_id,
               m.name AS medicine, b.name AS barangay
        {ACTIVITY_FROM}
        WHERE {where}
        ORDER BY sm.created_at DESC, sm.id DESC
        LIMIT %s OFFSET %s
        """,
        (*args, per_page, (page - 1) * per_page),
    )
    return render_template(
        "city/activity.html",
        rows=rows,
        totals=totals,
        total=total,
        page=page,
        pages=pages,
        filters=filters,
        barangays=query("SELECT id, name FROM barangays ORDER BY name"),
    )


@city_bp.route("/activity/export.csv")
def export_activity():
    _, where, args = _activity_filters()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Type", "Medicine", "Quantity", "Barangay", "By", "Note", "Request #"])
    for r in query(
        f"""
        SELECT sm.created_at, sm.movement, m.name AS medicine, sm.quantity, b.name AS barangay,
               sm.performed_by, sm.note, sm.request_id
        {ACTIVITY_FROM}
        WHERE {where}
        ORDER BY sm.created_at DESC, sm.id DESC
        """,
        args,
    ):
        writer.writerow([r["created_at"].strftime("%Y-%m-%d %H:%M"), r["movement"], r["medicine"], r["quantity"],
                         r["barangay"] or "City", r["performed_by"] or "", r["note"] or "", r["request_id"] or ""])
    return Response(
        "\ufeff" + out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=stock-activity-{date.today().isoformat()}.csv"},
    )


# ----------------------------------------------------------------- supply distribution

@city_bp.route("/supply", methods=["GET", "POST"])
def supply():
    if request.method == "POST":
        try:
            barangay_id = parse_id(request.form.get("barangay"), "barangay")
            medicine_id = parse_id(request.form.get("medicine"), "medicine")
            quantity = parse_quantity(request.form.get("quantity"))
            med = distribute(barangay_id, medicine_id, quantity)
            flash(f"Distributed {quantity:,} × {med['name']}.", "success")
            return redirect(url_for("city.supply"))
        except InventoryError as e:
            flash(str(e), "error")

    cfg = current_app.config
    horizon = cfg["FORECAST_HORIZON_WEEKS"]
    barangays = query("SELECT id, name FROM barangays ORDER BY name")
    medicines = query("SELECT id, name, stock FROM medicines WHERE is_active = 1 ORDER BY name")

    # per barangay & medicine: current stock and average weekly demand (last 8 weeks)
    context = {}
    for r in query(
        """
        SELECT barangay_id, medicine_id, SUM(quantity) / 8 AS weekly
        FROM requests
        WHERE status IN %s AND requested_at >= NOW() - INTERVAL 8 WEEK
        GROUP BY barangay_id, medicine_id
        """,
        (fc.DEMAND_STATUSES,),
    ):
        context[f"{r['barangay_id']}-{r['medicine_id']}"] = {"weekly": round(float(r["weekly"]), 1), "stock": 0}
    for r in query("SELECT barangay_id, medicine_id, stock FROM barangay_inventory"):
        context.setdefault(f"{r['barangay_id']}-{r['medicine_id']}", {"weekly": 0})["stock"] = r["stock"]

    recent = query(
        """
        SELECT r.id, r.quantity, r.source, COALESCE(r.action_at, r.requested_at) AS released_at, r.action_by,
               b.name AS barangay, m.name AS medicine
        FROM requests r
        JOIN barangays b ON b.id = r.barangay_id
        JOIN medicines m ON m.id = r.medicine_id
        WHERE r.status = 'released'
        ORDER BY released_at DESC, r.id DESC
        LIMIT 15
        """
    )

    return render_template(
        "city/supply.html",
        barangays=barangays,
        medicines=medicines,
        context=context,
        recent=recent,
        horizon=horizon,
        safety=cfg["SAFETY_STOCK_RATIO"],
        form=request.form,
        preselect=request.args,
    )


# ----------------------------------------------------------------- reports

def _horizon_arg():
    weeks = request.args.get("weeks", current_app.config["FORECAST_HORIZON_WEEKS"], type=int)
    return weeks if weeks in HORIZON_CHOICES else current_app.config["FORECAST_HORIZON_WEEKS"]


@city_bp.route("/reports")
def reports():
    horizon = _horizon_arg()
    rows = fc.medicine_outlook(horizon)
    summary = {k: sum(1 for r in rows if r["risk"] == k) for k in fc.RISK_LABELS}
    return render_template(
        "city/reports.html",
        rows=rows,
        charted=[r for r in rows if r["risk"] != "idle"],
        horizon=horizon,
        horizons=HORIZON_CHOICES,
        summary=summary,
        restock_units=sum(r["recommended"] for r in rows),
        arima_count=sum(1 for r in rows if r["method"].startswith("ARIMA")),
        safety_pct=int(current_app.config["SAFETY_STOCK_RATIO"] * 100),
    )


@city_bp.route("/reports/export.csv")
def export_report():
    horizon = _horizon_arg()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Medicine", "City stock", "Avg weekly demand (8 wks)", f"Forecast demand ({horizon} wks)",
                     "Weeks of cover", "Risk", "Recommended restock", "Method"])
    for r in fc.medicine_outlook(horizon):
        writer.writerow([
            r["name"], r["stock"], f"{r['recent_avg']:.1f}", r["demand"],
            f"{r['cover_weeks']:.1f}" if r["cover_weeks"] is not None else "",
            r["risk_label"], r["recommended"], r["method"],
        ])
    filename = f"replenishment-report-{date.today().isoformat()}.csv"
    return Response(
        "\ufeff" + out.getvalue(),  # BOM so Excel opens it as UTF-8
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ----------------------------------------------------------------- city stock

@city_bp.route("/stock")
def add_stock():
    show_archived = request.args.get("archived") == "1"
    rows = fc.medicine_outlook(include_archived=True)
    active = [r for r in rows if r["is_active"]]
    archived = [r for r in rows if not r["is_active"]]
    movements = query(
        """
        SELECT sm.movement, sm.quantity, sm.performed_by, sm.note, sm.created_at,
               m.name AS medicine, b.name AS barangay
        FROM stock_movements sm
        JOIN medicines m ON m.id = sm.medicine_id
        LEFT JOIN barangays b ON b.id = sm.barangay_id
        WHERE sm.movement IN ('restock', 'release')
        ORDER BY sm.created_at DESC, sm.id DESC
        LIMIT 10
        """
    )
    return render_template(
        "city/add_stock.html",
        medicines=sorted(active, key=lambda r: r["name"]),
        table=rows if show_archived else active,
        archived_count=len(archived),
        show_archived=show_archived,
        total_units=sum(r["stock"] for r in active),
        movements=movements,
        horizon=current_app.config["FORECAST_HORIZON_WEEKS"],
        preselect=request.args.get("medicine", type=int),
    )


@city_bp.route("/stock/medicine", methods=["POST"])
def create_medicine():
    try:
        initial = request.form.get("initial_stock") or "0"
        initial = 0 if initial.strip() == "0" else parse_quantity(initial, 1_000_000)
        name = add_medicine(request.form.get("medicine_name"), initial)
        flash(f"Added {name}.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return redirect(url_for("city.add_stock"))


@city_bp.route("/stock/restock", methods=["POST"])
def restock_medicine():
    try:
        medicine_id = parse_id(request.form.get("medicine_id"), "medicine")
        quantity = parse_quantity(request.form.get("quantity"), 1_000_000)
        med = restock(medicine_id, quantity, request.form.get("note"))
        flash(f"Added {quantity:,} units to {med['name']}.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return back("city.add_stock")


@city_bp.route("/stock/<int:id>/archive", methods=["POST"])
def archive_medicine(id):
    try:
        set_medicine_active(id, False)
        flash("Medicine archived. Its history is kept for reports.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return back("city.add_stock")


@city_bp.route("/stock/<int:id>/restore", methods=["POST"])
def restore_medicine(id):
    try:
        set_medicine_active(id, True)
        flash("Medicine restored.", "success")
    except InventoryError as e:
        flash(str(e), "error")
    return back("city.add_stock", archived=1)


# ----------------------------------------------------------------- users

@city_bp.route("/users")
def manage_users():
    users = query(
        """
        SELECT u.id, u.username, u.role, u.created_at, u.last_login_at, b.name AS barangay
        FROM users u LEFT JOIN barangays b ON b.id = u.barangay_id
        ORDER BY u.role DESC, u.username
        """
    )
    return render_template(
        "city/users.html",
        users=users,
        barangays=query("SELECT name FROM barangays ORDER BY name"),
    )


@city_bp.route("/users/add", methods=["POST"])
def add_user():
    username = " ".join((request.form.get("username") or "").split())
    password = request.form.get("password") or ""
    role = request.form.get("role")
    barangay_name = " ".join((request.form.get("barangay") or "").split())

    error = None
    if not 3 <= len(username) <= 60:
        error = "Username must be 3–60 characters."
    elif len(password) < 6:
        error = "Password must be at least 6 characters."
    elif role not in ("city", "barangay"):
        error = "Choose a role."
    elif role == "barangay" and not barangay_name:
        error = "Enter the barangay this account belongs to."
    if error:
        flash(error, "error")
        return redirect(url_for("city.manage_users"))

    try:
        with transaction() as cur:
            barangay_id = None
            if role == "barangay":
                cur.execute("SELECT id FROM barangays WHERE name = %s", (barangay_name,))
                row = cur.fetchone()
                if row:
                    barangay_id = row["id"]
                else:
                    cur.execute("INSERT INTO barangays (name) VALUES (%s)", (barangay_name,))
                    barangay_id = cur.lastrowid
            cur.execute(
                "INSERT INTO users (username, password_hash, role, barangay_id) VALUES (%s, %s, %s, %s)",
                (username, generate_password_hash(password), role, barangay_id),
            )
        flash(f"Account “{username}” created.", "success")
    except pymysql.err.IntegrityError:
        flash(f"The username “{username}” is already taken.", "error")
    return redirect(url_for("city.manage_users"))


@city_bp.route("/users/<int:id>/password", methods=["POST"])
def reset_password(id):
    password = request.form.get("password") or ""
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
    else:
        with transaction() as cur:
            changed = cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s", (generate_password_hash(password), id))
        flash("Password updated." if changed else "User not found.", "success" if changed else "error")
    return redirect(url_for("city.manage_users"))


@city_bp.route("/users/<int:id>/delete", methods=["POST"])
def delete_user(id):
    user = query("SELECT id, username, role FROM users WHERE id = %s", (id,), one=True)
    if not user:
        flash("User not found.", "error")
    elif user["id"] == g.user["id"]:
        flash("You cannot delete your own account while signed in.", "error")
    elif user["role"] == "city" and scalar("SELECT COUNT(*) FROM users WHERE role = 'city'") <= 1:
        flash("At least one city administrator account must remain.", "error")
    else:
        with transaction() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (id,))
        flash(f"Account “{user['username']}” deleted.", "success")
    return redirect(url_for("city.manage_users"))
