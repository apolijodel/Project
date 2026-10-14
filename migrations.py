"""
Idempotent schema upgrades, so databases created by an earlier version keep working.
Run automatically when the app starts and at the end of setup_db.py.
"""

REQUEST_EVENTS = """
CREATE TABLE IF NOT EXISTS request_events (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    request_id  INT NOT NULL,
    status      ENUM('pending', 'approved', 'released', 'rejected', 'cancelled') NOT NULL,
    actor       VARCHAR(60) NULL,
    note        VARCHAR(255) NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_events_request (request_id, created_at),
    KEY idx_events_created (created_at),
    CONSTRAINT fk_events_request FOREIGN KEY (request_id)
        REFERENCES requests (id) ON DELETE CASCADE
) ENGINE=InnoDB
"""


def _has_column(cur, table, column):
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cur.fetchone()["n"] > 0


def upgrade(conn):
    """Bring the schema up to date. Returns a list of what changed."""
    changes = []
    with conn.cursor() as cur:
        if not _has_column(cur, "users", "notifications_seen_at"):
            cur.execute("ALTER TABLE users ADD COLUMN notifications_seen_at DATETIME NULL")
            changes.append("users.notifications_seen_at")

        cur.execute(REQUEST_EVENTS)

        # Requests created before the event log existed get their known history.
        added = cur.execute(
            """
            INSERT INTO request_events (request_id, status, actor, note, created_at)
            SELECT r.id, 'pending', NULL, NULL, r.requested_at
            FROM requests r
            WHERE r.source = 'request'
              AND NOT EXISTS (SELECT 1 FROM request_events e WHERE e.request_id = r.id)
            """
        )
        added += cur.execute(
            """
            INSERT INTO request_events (request_id, status, actor, note, created_at)
            SELECT r.id, r.status, r.action_by, r.remarks, COALESCE(r.action_at, r.requested_at)
            FROM requests r
            WHERE r.status <> 'pending'
              AND NOT EXISTS (SELECT 1 FROM request_events e
                              WHERE e.request_id = r.id AND e.status = r.status)
            """
        )
        if added:
            changes.append(f"request_events (+{added} from existing requests)")

        # Releases made before the stock log existed, so the activity log shows them too.
        moved = cur.execute(
            """
            INSERT INTO stock_movements (medicine_id, barangay_id, request_id, movement, quantity,
                                         performed_by, note, created_at)
            SELECT r.medicine_id, r.barangay_id, r.id, 'release', r.quantity, r.action_by,
                   'Backfilled from request history', COALESCE(r.action_at, r.requested_at)
            FROM requests r
            WHERE r.status = 'released'
              AND NOT EXISTS (SELECT 1 FROM stock_movements sm WHERE sm.request_id = r.id)
            """
        )
        if moved:
            changes.append(f"stock_movements (+{moved} releases from request history)")
    return changes
