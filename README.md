# Pasig DASRS — Demand Aggregation & Supply Replenishment System

Flask web app for the Pasig City Health Office. Barangays request medicines, the city approves
and releases them, and ARIMA forecasts flag shortages before they happen.

## Run it locally (Windows + XAMPP)

1. Open the **XAMPP Control Panel** and start **MySQL**.
2. In this folder:

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python setup_db.py          # creates `pasig_dasrs_db` and migrates database.db
   .venv\Scripts\python app.py               # http://127.0.0.1:5000
   ```

`setup_db.py` only migrates on the first run (when the MySQL `users` table is empty), so it is
safe to run again. Other options:

| Command | What it does |
|---|---|
| `python setup_db.py --demo` | Adds 26 weeks of generated request history so the forecasts have data to learn from |
| `python setup_db.py --reset` | **Drops** `pasig_dasrs_db` and rebuilds it from `database.db` |

You can also import `database/schema.sql` directly in phpMyAdmin.

### Accounts

Demo accounts (one per user level) are created by `setup_db.py` and listed on the login page
for one-click sign-in:

| User level | Username | Password | Can do |
|---|---|---|---|
| City Administrator | `admin` | `admin123` | Approve/reject/release requests, distribution, city stock, forecasts & reports, barangays, user accounts |
| Barangay (Kapitolyo) | `kapitolyo.bhw` | `brgy123` | Request supplies, cancel own requests, track inventory and dispensing — own barangay only |
| Barangay (Ugong) | `ugong.bhw` | `brgy123` | same, for Ugong |
| Barangay (Bagong Ilog) | `bagongilog.bhw` | `brgy123` | same, for Bagong Ilog |

Hide the demo list with `SHOW_DEMO_ACCOUNTS=0` and change these passwords before real use.
Accounts from the old SQLite database are kept too (e.g. `cityadmin / 123`, `Ugong / 123`).

### Settings

Defaults match XAMPP. Override with environment variables if needed:
`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `SECRET_KEY`, `PORT`, `FLASK_DEBUG=1`.
Business rules (low-stock threshold, forecast horizon, safety stock) are in `config.py`.

## How it works

- **Workflow:** pending → approved → released (or rejected / cancelled). Releasing moves stock
  from the city to the barangay in one database transaction; stock can never go negative and a
  request can't be released twice.
- **Requisitions:** barangays submit several medicines at once. *Suggested for you* pre-fills
  quantities that cover 2 weeks of use (from dispensing records, else past requests) + 20% safety.
- **Bulk actions:** on the city request queue, select rows (shift-click for a range) to approve,
  reject, release or print slips in one go. Releases go oldest-first; any without stock are skipped.
- **Timeline & notifications:** every status change is stored in `request_events`, shown as a
  progress timeline on each request and as a notification bell for city and barangay users.
- **Printable RIS:** a Requisition and Issue Slip (one per barangay, A4) for approved/released requests.
- **Audit trail:** every restock, release and dispense is written to `stock_movements`, browsable
  and exportable in **Activity Log**.
- **Schema upgrades** run automatically on startup (`migrations.py`), so older databases keep working.
- **Forecast:** units requested per completed week (last 52 weeks) → ARIMA(1,1,1) per medicine,
  or a 4-week moving average when history is short. Results are cached and only refit when the
  data changes.
- **Recommendation:** safety target = forecast × 1.2; restock = target − city stock.
  Risk is *Critical* below the forecast, *Low* below the safety target, otherwise *Adequate*.

## Project layout

```
app.py              app factory, template filters, error pages
config.py           settings and business rules
db.py               MySQL connection + query helpers
security.py         login session, role guards, CSRF
setup_db.py         create database, migrate SQLite, demo data
database/schema.sql MySQL schema
blueprints/         auth, city, barangay routes
services/           forecast.py (ARIMA + decision support), inventory.py (stock workflow)
templates/          Jinja pages (base.html + _macros.html shared)
static/             style.css, app.js, vendored Chart.js / Font Awesome / font (works offline)
```
