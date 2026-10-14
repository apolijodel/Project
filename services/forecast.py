"""
Weekly demand forecasting (ARIMA) and replenishment decision support.

Demand = quantities requested by barangays (pending, approved and released requests;
rejected and cancelled ones are excluded), summed per ISO week. Weeks with no
requests count as zero demand. Each medicine gets an ARIMA(1,1,1) model when it has
enough history, otherwise a 4-week moving average.

Fitting is cached and only recomputed when the demand data or the current week changes.
"""
import math
import threading
import warnings
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from flask import current_app

from db import query

DEMAND_STATUSES = ("pending", "approved", "released")
MAX_HORIZON = 12
ARIMA_ORDER = (1, 1, 1)
MIN_WEEKS_FOR_ARIMA = 12
MIN_ACTIVE_WEEKS_FOR_ARIMA = 6

RISK_ORDER = {"critical": 0, "low": 1, "adequate": 2, "idle": 3}
RISK_LABELS = {"critical": "Critical", "low": "Low", "adequate": "Adequate", "idle": "No demand"}

_cache = {"key": None, "forecasts": None}
_lock = threading.Lock()


def week_start(d):
    return d - timedelta(days=d.weekday())


@dataclass
class Forecast:
    medicine_id: int
    weeks: list        # week-start dates of the history window
    history: list      # units requested per week
    future: list       # forecast units per week, MAX_HORIZON long
    method: str

    def total(self, horizon):
        return int(round(sum(self.future[:horizon])))

    def recent_average(self, weeks=8):
        recent = self.history[-weeks:]
        return sum(recent) / len(recent) if recent else 0.0

    @property
    def future_weeks(self):
        last = self.weeks[-1]
        return [last + timedelta(weeks=i + 1) for i in range(MAX_HORIZON)]


def _fit(values):
    values = np.asarray(values, dtype=float)
    active = int((values > 0).sum())
    if active == 0:
        return [0.0] * MAX_HORIZON, "No demand recorded"

    if len(values) >= MIN_WEEKS_FOR_ARIMA and active >= MIN_ACTIVE_WEEKS_FOR_ARIMA:
        try:
            from statsmodels.tsa.arima.model import ARIMA  # slow import, so load lazily

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = ARIMA(values, order=ARIMA_ORDER).fit()
                out = np.asarray(result.forecast(MAX_HORIZON), dtype=float)
            if np.all(np.isfinite(out)):
                return np.clip(out, 0, None).tolist(), "ARIMA(1,1,1)"
        except Exception as exc:  # fall back rather than fail the page
            current_app.logger.warning("ARIMA failed, using moving average: %s", exc)

    avg = float(values[-4:].mean())
    return [avg] * MAX_HORIZON, "4-week moving average"


def _fingerprint():
    # Only completed weeks feed the models, so requests made this week don't force a refit.
    current = week_start(date.today())
    row = query(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(quantity), 0) AS units, COALESCE(MAX(id), 0) AS last_id
        FROM requests WHERE status IN %s AND requested_at < %s
        """,
        (DEMAND_STATUSES, current),
        one=True,
    )
    meds = query("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS last_id FROM medicines", one=True)
    return (current, row["n"], row["units"], row["last_id"], meds["n"], meds["last_id"])


def _compute():
    lookback = current_app.config["FORECAST_LOOKBACK_WEEKS"]
    # History ends with the last *complete* week: a partly elapsed week would look like a
    # demand drop and drag the forecast down. The forecast starts with the current week.
    current = week_start(date.today())
    start = current - timedelta(weeks=lookback)
    weeks = [start + timedelta(weeks=i) for i in range(lookback)]
    index = {w: i for i, w in enumerate(weeks)}

    rows = query(
        """
        SELECT medicine_id,
               DATE(requested_at) - INTERVAL WEEKDAY(requested_at) DAY AS wk,
               SUM(quantity) AS qty
        FROM requests
        WHERE status IN %s AND requested_at >= %s AND requested_at < %s
        GROUP BY medicine_id, wk
        """,
        (DEMAND_STATUSES, start, current),
    )
    series = {m["id"]: [0.0] * lookback for m in query("SELECT id FROM medicines")}
    for r in rows:
        i = index.get(r["wk"])
        if i is not None and r["medicine_id"] in series:
            series[r["medicine_id"]][i] += float(r["qty"])

    forecasts = {}
    for medicine_id, values in series.items():
        future, method = _fit(values)
        forecasts[medicine_id] = Forecast(medicine_id, weeks, values, future, method)
    return forecasts


def all_forecasts():
    key = _fingerprint()
    if _cache["key"] == key:
        return _cache["forecasts"]
    with _lock:
        if _cache["key"] != key:
            _cache["forecasts"] = _compute()
            _cache["key"] = key
    return _cache["forecasts"]


def get_forecast(medicine_id):
    return all_forecasts().get(medicine_id)


def assess(stock, forecast, horizon=None):
    """Compare stock with forecast demand and recommend a replenishment quantity."""
    cfg = current_app.config
    horizon = horizon or cfg["FORECAST_HORIZON_WEEKS"]
    demand = forecast.total(horizon) if forecast else 0
    weekly = demand / horizon if horizon else 0
    target = math.ceil(demand * (1 + cfg["SAFETY_STOCK_RATIO"]))

    if demand <= 0:
        risk = "idle"
    elif stock < demand:
        risk = "critical"
    elif stock < target:
        risk = "low"
    else:
        risk = "adequate"

    return {
        "demand": demand,
        "weekly": weekly,
        "target": target,
        "recommended": max(target - stock, 0),
        "cover_weeks": (stock / weekly) if weekly > 0 else None,
        "risk": risk,
        "risk_label": RISK_LABELS[risk],
        "method": forecast.method if forecast else "No demand recorded",
    }


def medicine_outlook(horizon=None, include_archived=False):
    """Forecast + assessment for every medicine, most urgent first."""
    where = "" if include_archived else "WHERE is_active = 1"
    medicines = query(f"SELECT id, name, stock, is_active FROM medicines {where} ORDER BY name")
    forecasts = all_forecasts()
    rows = []
    for m in medicines:
        fc = forecasts.get(m["id"])
        rows.append({**m, **assess(m["stock"], fc, horizon),
                     "recent_avg": fc.recent_average() if fc else 0})
    rows.sort(key=lambda r: (RISK_ORDER[r["risk"]], -r["recommended"], r["name"]))
    return rows


def barangay_suggestions(barangay_id):
    """
    What a barangay should request now: enough to cover BARANGAY_COVER_WEEKS of use plus
    safety stock, minus what it holds. Usage = units dispensed in the last 8 weeks when the
    barangay records dispensing, otherwise units it requested.
    """
    cfg = current_app.config
    weeks, safety = cfg["BARANGAY_COVER_WEEKS"], cfg["SAFETY_STOCK_RATIO"]
    threshold = cfg["LOW_STOCK_THRESHOLD"]
    rows = query(
        """
        SELECT m.id, m.name, bi.stock,
               COALESCE(req.units, 0) AS requested, COALESCE(used.units, 0) AS dispensed,
               EXISTS(SELECT 1 FROM requests p WHERE p.barangay_id = %s AND p.medicine_id = m.id
                      AND p.status = 'pending') AS has_pending
        FROM medicines m
        LEFT JOIN barangay_inventory bi ON bi.medicine_id = m.id AND bi.barangay_id = %s
        LEFT JOIN (
            SELECT medicine_id, SUM(quantity) AS units FROM requests
            WHERE barangay_id = %s AND status IN %s AND requested_at >= NOW() - INTERVAL 8 WEEK
            GROUP BY medicine_id
        ) req ON req.medicine_id = m.id
        LEFT JOIN (
            SELECT medicine_id, SUM(quantity) AS units FROM stock_movements
            WHERE barangay_id = %s AND movement = 'dispense' AND created_at >= NOW() - INTERVAL 8 WEEK
            GROUP BY medicine_id
        ) used ON used.medicine_id = m.id
        WHERE m.is_active = 1
        """,
        (barangay_id, barangay_id, barangay_id, DEMAND_STATUSES, barangay_id),
    )
    out = []
    for r in rows:
        if r["has_pending"]:
            continue
        stock = r["stock"] or 0
        weekly = (r["dispensed"] or r["requested"]) / 8
        if weekly > 0:
            suggested = math.ceil(weekly * weeks * (1 + safety)) - stock
            basis = "usage" if r["dispensed"] else "past requests"
        elif r["stock"] is not None and stock <= threshold:
            suggested, basis, weekly = threshold * 2 - stock, "low stock", 0
        else:
            continue
        if suggested <= 0:
            continue
        out.append({"id": r["id"], "name": r["name"], "stock": stock, "weekly": weekly,
                    "suggested": suggested, "basis": basis,
                    "cover_weeks": (stock / weekly) if weekly else None})
    out.sort(key=lambda s: (s["stock"] > 0, s["cover_weeks"] if s["cover_weeks"] is not None else 0, s["name"]))
    return out


def warm_cache(app):
    """Fit all models in the background so the first page view is fast."""
    def run():
        with app.app_context():
            try:
                all_forecasts()
                app.logger.info("Forecast cache ready")
            except Exception as exc:
                app.logger.warning("Forecast warm-up skipped: %s", exc)

    threading.Thread(target=run, name="forecast-warmup", daemon=True).start()
