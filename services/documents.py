"""Data for printable documents (Requisition and Issue Slip)."""
from datetime import date

from db import query


def requisition_slips(request_ids, barangay_id=None):
    """
    One slip per barangay for the given requests (rejected/cancelled ones are left out).
    Restrict to one barangay with barangay_id (used for barangay users).
    """
    if not request_ids:
        return []
    where, args = "r.id IN %s AND r.status NOT IN ('rejected', 'cancelled')", [tuple(request_ids)]
    if barangay_id:
        where += " AND r.barangay_id = %s"
        args.append(barangay_id)

    rows = query(
        f"""
        SELECT r.id, r.barangay_id, r.quantity, r.status, r.source, r.requested_at, r.remarks,
               m.name AS medicine, b.name AS barangay,
               (SELECT e.actor FROM request_events e
                 WHERE e.request_id = r.id AND e.status = 'pending' ORDER BY e.id LIMIT 1) AS requested_by,
               (SELECT e.actor FROM request_events e
                 WHERE e.request_id = r.id AND e.status = 'approved' ORDER BY e.id DESC LIMIT 1) AS approved_by,
               (SELECT e.actor FROM request_events e
                 WHERE e.request_id = r.id AND e.status = 'released' ORDER BY e.id DESC LIMIT 1) AS issued_by,
               (SELECT e.created_at FROM request_events e
                 WHERE e.request_id = r.id AND e.status = 'released' ORDER BY e.id DESC LIMIT 1) AS issued_at
        FROM requests r
        JOIN medicines m ON m.id = r.medicine_id
        JOIN barangays b ON b.id = r.barangay_id
        WHERE {where}
        ORDER BY b.name, r.requested_at, r.id
        """,
        args,
    )

    slips = {}
    for r in rows:
        slip = slips.setdefault(r["barangay_id"], {"barangay": r["barangay"], "lines": []})
        slip["lines"].append(r)

    out = []
    for slip in slips.values():
        lines = slip["lines"]
        first = min(lines, key=lambda l: l["requested_at"])
        issued = [l["issued_at"] for l in lines if l["issued_at"]]
        pick = lambda key: next((l[key] for l in lines if l[key]), None)
        out.append({
            **slip,
            "number": f"RIS-{first['requested_at']:%Y-%m}-{min(l['id'] for l in lines):05d}",
            "date": max(issued).date() if issued else date.today(),
            "requested_by": pick("requested_by"),
            "approved_by": pick("approved_by"),
            "issued_by": pick("issued_by"),
            "total_requested": sum(l["quantity"] for l in lines),
            "total_issued": sum(l["quantity"] for l in lines if l["status"] == "released"),
        })
    return out
