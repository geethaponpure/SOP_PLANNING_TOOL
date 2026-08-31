"""Staging layer — the MySQL snapshot of CRM that the API serves from.

Phase 1 of the sync-to-DB architecture (see ARCHITECTURE.md). The worker WRITES
here (from CRM); the API READS here (never touching CRM at request time).

Write strategy: each ``replace_*`` fully replaces the table's contents inside ONE
transaction (DELETE-all + INSERT-all + COMMIT), so API readers always see a
complete previous snapshot — never empty or half-loaded — and deletions in CRM
propagate automatically. ``sync_runs`` is append-only (the freshness log).

Read functions return rows keyed EXACTLY like the original CRM queries, so the
existing api/live.py consumers work unchanged.
"""
from __future__ import annotations

from datetime import datetime

from . import mysql_db


# ── sync-run log (append-only) ────────────────────────────────────────────────

def start_run(source: str) -> int | None:
    """Insert a 'running' sync_runs row; return its run_id (None if DB down)."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO sync_runs (source, started_at, status) VALUES (%s, %s, 'running')",
                            (source[:32], datetime.now()))
                return cur.lastrowid
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


def finish_run(run_id, status: str, row_count: int | None = None, error: str | None = None) -> None:
    if not run_id:
        return
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE sync_runs SET finished_at=%s, status=%s, row_count=%s, error=%s "
                            "WHERE run_id=%s",
                            (datetime.now(), status[:16], row_count,
                             (error or "")[:255] or None, int(run_id)))
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        pass


def last_sync(source: str) -> dict | None:
    """Latest run for a source: {status, started_at, finished_at, row_count, error}."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status, started_at, finished_at, row_count, error FROM sync_runs "
                            "WHERE source=%s ORDER BY run_id DESC LIMIT 1", (source,))
                r = cur.fetchone()
                if not r:
                    return None
                for k in ("started_at", "finished_at"):
                    if r.get(k) is not None:
                        r[k] = str(r[k])
                return r
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


# ── transactional full-replace helper ─────────────────────────────────────────

def _replace(table: str, columns: list[str], rows: list[tuple]) -> int:
    """DELETE-all + INSERT-all in one transaction. Readers keep seeing the old
    snapshot until COMMIT. Returns the number of rows written."""
    conn = mysql_db._connect()
    conn.autocommit(False)
    try:
        placeholders = ",".join(["%s"] * len(columns))
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}")
            for i in range(0, len(rows), 2000):
                cur.executemany(sql, rows[i:i + 2000])
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── stock_lots ────────────────────────────────────────────────────────────────

_STOCK_COLS = ["item_code", "item_desc", "organization", "org_code",
               "subinv", "lot", "qty", "aging_date", "age_days"]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _date_or_none(v):
    if v is None:
        return None
    return str(v)[:10] or None


def replace_stock_lots(crm_rows: list[dict]) -> int:
    """Replace stg_stock_lots with the CRM stock_lots rows (raw CRM keys in)."""
    data = [(
        str(r.get("ItemCode") or "")[:64], str(r.get("ItemDesc") or "")[:255],
        str(r.get("Organization") or "")[:120], str(r.get("OrgCode") or "")[:32],
        str(r.get("SubInv") or "")[:64], str(r.get("Lot") or "")[:80],
        round(_num(r.get("Qty")), 3), _date_or_none(r.get("AgingDate")),
        _int_or_none(r.get("AgeDays")),
    ) for r in crm_rows if r.get("ItemCode")]
    return _replace("stg_stock_lots", _STOCK_COLS, data)


def read_stock_lots() -> list[dict]:
    """Return the staged stock rows keyed like CRM stock_lots (so api/live.py
    consumers are unchanged)."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_desc, organization, org_code, subinv, lot, "
                            "qty, aging_date, age_days FROM stg_stock_lots")
                return [{"ItemCode": r["item_code"], "ItemDesc": r["item_desc"],
                         "Organization": r["organization"], "OrgCode": r["org_code"],
                         "SubInv": r["subinv"], "Lot": r["lot"], "Qty": r["qty"],
                         "AgingDate": r["aging_date"], "AgeDays": r["age_days"]}
                        for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── item_segments ─────────────────────────────────────────────────────────────

_SEG_COLS = ["item_code", "item_name", "division_target", "segment1", "segment2", "segment3"]


def replace_item_segments(crm_rows: list[dict]) -> int:
    """Replace stg_item_segments with the CRM item_segments rows (raw CRM keys in)."""
    data = [(
        str(r.get("ItemCode") or "")[:64], str(r.get("ItemName") or "")[:255],
        str(r.get("DivisionTarget") or "")[:64] or None, str(r.get("Segment1") or "")[:64] or None,
        str(r.get("Segment2") or "")[:64] or None, str(r.get("Segment3") or "")[:64] or None,
    ) for r in crm_rows if r.get("ItemCode")]
    # item_code is the PK; de-dupe keeping the last occurrence just in case.
    seen: dict = {}
    for row in data:
        seen[row[0]] = row
    return _replace("stg_item_segments", _SEG_COLS, list(seen.values()))


def read_item_segments() -> list[dict]:
    """Return the staged segment rows keyed like CRM item_segments."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_name, division_target, segment1, segment2, segment3 "
                            "FROM stg_item_segments")
                return [{"ItemCode": r["item_code"], "ItemName": r["item_name"],
                         "DivisionTarget": r["division_target"], "Segment1": r["segment1"],
                         "Segment2": r["segment2"], "Segment3": r["segment3"]}
                        for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── stock_details (full on-hand, BiStockDetail) ───────────────────────────────

_STOCK_DET_COLS = ["organization", "item_code", "item_desc", "subinv", "qty", "item_cost"]


def replace_stock_details(crm_rows: list[dict]) -> int:
    data = [(
        str(r.get("Organization") or "")[:120], str(r.get("ItemCode") or "")[:64],
        str(r.get("ItemDesc") or "")[:255], str(r.get("SubInv") or "")[:64],
        round(_num(r.get("Qty")), 3),
        (None if r.get("ItemCost") is None else round(_num(r.get("ItemCost")), 4)),
    ) for r in crm_rows if r.get("ItemCode")]
    return _replace("stg_stock_details", _STOCK_DET_COLS, data)


def read_stock_details() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT organization, item_code, item_desc, subinv, qty, item_cost "
                            "FROM stg_stock_details")
                return [{"Organization": r["organization"], "ItemCode": r["item_code"],
                         "ItemDesc": r["item_desc"], "SubInv": r["subinv"],
                         "Qty": r["qty"], "ItemCost": r["item_cost"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── item_business (item_code -> Business) ─────────────────────────────────────

def replace_item_business(crm_rows: list[dict]) -> int:
    data = [(str(r.get("ItemCode") or "")[:64], str(r.get("Business") or "")[:120] or None)
            for r in crm_rows if r.get("ItemCode")]
    return _replace("stg_item_business", ["item_code", "business"], data)


def read_item_business() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, business FROM stg_item_business")
                return [{"ItemCode": r["item_code"], "Business": r["business"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── pto_pts (item master + PTO/PTS flag) ──────────────────────────────────────

_PTO_COLS = ["item_id", "item_code", "item_name", "uom",
             "segment1", "segment2", "segment3", "segment4", "itemtype"]


def replace_pto_pts(crm_rows: list[dict]) -> int:
    data = [(
        str(r.get("ItemId") or "")[:64], str(r.get("Item_Code") or "")[:64],
        str(r.get("Item_Name") or "")[:255], str(r.get("UOM") or "")[:32],
        str(r.get("Segment1") or "")[:64] or None, str(r.get("Segment2") or "")[:64] or None,
        str(r.get("Segment3") or "")[:64] or None, str(r.get("Segment4") or "")[:64] or None,
        str(r.get("Itemtype") or "")[:16] or None,
    ) for r in crm_rows]
    return _replace("stg_pto_pts", _PTO_COLS, data)


def read_pto_pts() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_id, item_code, item_name, uom, segment1, segment2, "
                            "segment3, segment4, itemtype FROM stg_pto_pts")
                return [{"ItemId": r["item_id"], "Item_Code": r["item_code"],
                         "Item_Name": r["item_name"], "UOM": r["uom"],
                         "Segment1": r["segment1"], "Segment2": r["segment2"],
                         "Segment3": r["segment3"], "Segment4": r["segment4"],
                         "Itemtype": r["itemtype"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── stock_aged (aged on-hand; param = aged_rm_days, a static setting) ──────────

_STOCK_AGED_COLS = ["organization", "item_code", "item_desc", "subinv", "qty", "item_cost", "max_age_days"]


def replace_stock_aged(crm_rows: list[dict]) -> int:
    data = [(
        str(r.get("Organization") or "")[:120], str(r.get("ItemCode") or "")[:64],
        str(r.get("ItemDesc") or "")[:255], str(r.get("SubInv") or "")[:64],
        round(_num(r.get("Qty")), 3),
        (None if r.get("ItemCost") is None else round(_num(r.get("ItemCost")), 4)),
        _int_or_none(r.get("MaxAgeDays")),
    ) for r in (crm_rows or []) if r.get("ItemCode")]
    return _replace("stg_stock_aged", _STOCK_AGED_COLS, data)


def read_stock_aged() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT organization, item_code, item_desc, subinv, qty, item_cost, max_age_days "
                            "FROM stg_stock_aged")
                return [{"Organization": r["organization"], "ItemCode": r["item_code"],
                         "ItemDesc": r["item_desc"], "SubInv": r["subinv"], "Qty": r["qty"],
                         "ItemCost": r["item_cost"], "MaxAgeDays": r["max_age_days"]}
                        for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── vooki_division_items (param = vooki_business, a static setting) ────────────

def replace_vooki_items(crm_rows: list[dict]) -> int:
    data = [(str(r.get("ItemCode") or "")[:64], str(r.get("ItemDesc") or "")[:255])
            for r in (crm_rows or []) if r.get("ItemCode")]
    return _replace("stg_vooki_items", ["item_code", "item_desc"], data)


def read_vooki_items() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_desc FROM stg_vooki_items")
                return [{"ItemCode": r["item_code"], "ItemDesc": r["item_desc"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── soc_schedule (no params) ──────────────────────────────────────────────────

def replace_soc_schedule(crm_rows: list[dict]) -> int:
    data = [(
        str(r.get("ItemCode") or "")[:64], str(r.get("ItemDesc") or "")[:255],
        _date_or_none(r.get("ScheduleDate")), round(_num(r.get("Qty")), 3),
    ) for r in (crm_rows or [])]
    return _replace("stg_soc_schedule", ["item_code", "item_desc", "schedule_date", "qty"], data)


def read_soc_schedule() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_desc, schedule_date, qty FROM stg_soc_schedule")
                return [{"ItemCode": r["item_code"], "ItemDesc": r["item_desc"],
                         "ScheduleDate": r["schedule_date"], "Qty": r["qty"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── "Refresh now" queue (used by the worker's poller in Phase 4) ───────────────

def request_refresh(source: str = "all") -> bool:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO sync_requests (source, requested_at, status) VALUES (%s, %s, 'pending')",
                            (source[:32], datetime.now()))
            return True
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return False


def claim_pending_requests() -> list[dict]:
    """Mark all pending refresh requests as done and return them (worker poller)."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, source FROM sync_requests WHERE status='pending' ORDER BY id")
                rows = cur.fetchall()
                if rows:
                    cur.execute("UPDATE sync_requests SET status='done', claimed_at=%s WHERE status='pending'",
                                (datetime.now(),))
                return list(rows)
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []
