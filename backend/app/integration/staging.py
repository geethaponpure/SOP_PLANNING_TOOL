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

import json
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

def _replace(table: str, columns: list[str], rows: list[tuple],
             where: str = "", where_params: tuple = ()) -> int:
    """DELETE + INSERT-all in one transaction. Readers keep seeing the old snapshot
    until COMMIT. With ``where`` only that slice is replaced (e.g. one acc_year/jc);
    otherwise the whole table. Returns the number of rows written."""
    conn = mysql_db._connect()
    conn.autocommit(False)
    try:
        placeholders = ",".join(["%s"] * len(columns))
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        del_sql = f"DELETE FROM {table}" + (f" WHERE {where}" if where else "")
        with conn.cursor() as cur:
            cur.execute(del_sql, where_params)
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

_SEG_COLS = ["item_code", "item_name", "division_target", "segment1", "segment2", "segment3",
             "segment4"]


def replace_item_segments(crm_rows: list[dict]) -> int:
    """Replace stg_item_segments with the CRM item_segments rows (raw CRM keys in)."""
    data = [(
        str(r.get("ItemCode") or "")[:64], str(r.get("ItemName") or "")[:255],
        str(r.get("DivisionTarget") or "")[:64] or None, str(r.get("Segment1") or "")[:64] or None,
        str(r.get("Segment2") or "")[:64] or None, str(r.get("Segment3") or "")[:64] or None,
        str(r.get("Segment4") or "")[:64] or None,
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
                cur.execute("SELECT item_code, item_name, division_target, segment1, segment2, segment3, "
                            "segment4 FROM stg_item_segments")
                return [{"ItemCode": r["item_code"], "ItemName": r["item_name"],
                         "DivisionTarget": r["division_target"], "Segment1": r["segment1"],
                         "Segment2": r["segment2"], "Segment3": r["segment3"],
                         "Segment4": r["segment4"]}
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


# ── sync_context (the planning context the worker last synced for) ─────────────

def write_context(ctx: dict) -> None:
    """Upsert the single-row planning context (plan_jc / acc_year / windows)."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "REPLACE INTO sync_context (id, plan_jc, acc_year, soc_from, soc_to, "
                    "freeze_date, intransit_from, blanket_po_qty, computed_at) "
                    "VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (ctx.get("plan_jc"), ctx.get("acc_year"), ctx.get("soc_from"), ctx.get("soc_to"),
                     str(ctx.get("freeze_date") or "")[:20] or None, ctx.get("intransit_from"),
                     ctx.get("blanket_po_qty"), datetime.now()))
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        pass


def read_context() -> dict | None:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT plan_jc, acc_year, soc_from, soc_to, freeze_date, "
                            "intransit_from, blanket_po_qty, computed_at FROM sync_context WHERE id=1")
                r = cur.fetchone()
                if r:
                    for k in ("soc_from", "soc_to", "intransit_from", "computed_at"):
                        if r.get(k) is not None:
                            r[k] = str(r[k])
                return r
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


# ── projection (keyed by acc_year + jc) ───────────────────────────────────────

_PROJ_COLS = ["acc_year", "jc", "approved", "item_name", "segment2", "segment3",
              "current_q", "next1_q", "next2_q"]


def replace_projection(acc_year: str, jc: int, crm_rows: list[dict], approved: bool = True) -> int:
    appr = 1 if approved else 0
    data = [(
        acc_year, int(jc), appr, str(r.get("ItemName") or "")[:255],
        str(r.get("Segment2") or "")[:64] or None, str(r.get("Segment3") or "")[:64] or None,
        round(_num(r.get("CurrentQ")), 3), round(_num(r.get("Next1Q")), 3), round(_num(r.get("Next2Q")), 3),
    ) for r in (crm_rows or []) if r.get("ItemName")]
    # replace ONLY this (acc_year, jc, approved) slice, leaving other cycles intact
    return _replace("stg_projection", _PROJ_COLS, data,
                    where="acc_year=%s AND jc=%s AND approved=%s", where_params=(acc_year, int(jc), appr))


def read_projection(acc_year: str, jc: int, approved: bool = True) -> list[dict]:
    appr = 1 if approved else 0
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_name, segment2, segment3, current_q, next1_q, next2_q "
                            "FROM stg_projection WHERE acc_year=%s AND jc=%s AND approved=%s",
                            (acc_year, int(jc), appr))
                return [{"ItemName": r["item_name"], "Segment2": r["segment2"], "Segment3": r["segment3"],
                         "CurrentQ": r["current_q"], "Next1Q": r["next1_q"], "Next2Q": r["next2_q"]}
                        for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def read_projection_all(acc_year: str, approved: bool = True) -> list[dict]:
    """Every staged JC of one accounting year, item level — the My-Dashboard
    projection-accuracy trend (JC1..current)."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT jc, item_name, segment2, segment3, current_q "
                            "FROM stg_projection WHERE acc_year=%s AND approved=%s",
                            (acc_year, 1 if approved else 0))
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── projection ROWS (per item x collector — Projection-vs-Sales) ──────────────

_PROJ_ROWS_COLS = ["acc_year", "jc", "item_name", "collector", "segment2", "segment3",
                   "current_q", "next1_q", "next2_q"]


def replace_projection_rows(acc_year: str, jc: int, crm_rows: list[dict]) -> int:
    data = [(
        acc_year, int(jc), str(r.get("ItemName") or "")[:255], str(r.get("Collector") or "")[:400] or None,
        str(r.get("Segment2") or "")[:64] or None, str(r.get("Segment3") or "")[:64] or None,
        round(_num(r.get("CurrentQ")), 3), round(_num(r.get("Next1Q")), 3), round(_num(r.get("Next2Q")), 3),
    ) for r in (crm_rows or []) if r.get("ItemName")]
    return _replace("stg_projection_rows", _PROJ_ROWS_COLS, data,
                    where="acc_year=%s AND jc=%s", where_params=(acc_year, int(jc)))


def read_projection_rows(acc_year: str, jc: int) -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_name, collector, segment2, segment3, current_q, next1_q, next2_q "
                            "FROM stg_projection_rows WHERE acc_year=%s AND jc=%s", (acc_year, int(jc)))
                return [{"ItemName": r["item_name"], "Collector": r["collector"],
                         "Segment2": r["segment2"], "Segment3": r["segment3"],
                         "CurrentQ": r["current_q"], "Next1Q": r["next1_q"], "Next2Q": r["next2_q"]}
                        for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── SOC pending (scope 'all' | 'mfg', current window) ─────────────────────────

def replace_soc_pending(scoped: dict) -> int:
    """scoped = {'all': [...], 'mfg': [...]} of CRM despatch-pending rows."""
    data = []
    for scope in ("all", "mfg"):
        for r in (scoped.get(scope) or []):
            if r.get("ItemCode"):
                data.append((scope, str(r.get("ItemCode") or "")[:64],
                             str(r.get("ItemDesc") or "")[:255], round(_num(r.get("PendingQty")), 3)))
    return _replace("stg_soc_pending", ["scope", "item_code", "item_desc", "pending_qty"], data)


def read_soc_pending(scope: str) -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_desc, pending_qty FROM stg_soc_pending WHERE scope=%s",
                            (scope,))
                return [{"ItemCode": r["item_code"], "ItemDesc": r["item_desc"],
                         "PendingQty": r["pending_qty"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── post-freeze open SOC detail (current freeze) ──────────────────────────────

_SOC_DET_COLS = ["item_code", "item_name", "soc_qty", "soc_count", "last_soc", "segment2", "segment3"]


def replace_soc_detail(crm_rows: list[dict]) -> int:
    data = [(
        str(r.get("ItemCode") or "")[:64], str(r.get("ItemName") or "")[:255],
        round(_num(r.get("SocQty")), 3), _int_or_none(r.get("SocCount")),
        str(r.get("LastSoc") or "")[:30] or None,
        str(r.get("Segment2") or "")[:64] or None, str(r.get("Segment3") or "")[:64] or None,
    ) for r in (crm_rows or []) if r.get("ItemCode")]
    return _replace("stg_soc_detail", _SOC_DET_COLS, data)


def read_soc_detail() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_name, soc_qty, soc_count, last_soc, segment2, segment3 "
                            "FROM stg_soc_detail")
                return [{"ItemCode": r["item_code"], "ItemName": r["item_name"], "SocQty": r["soc_qty"],
                         "SocCount": r["soc_count"], "LastSoc": r["last_soc"],
                         "Segment2": r["segment2"], "Segment3": r["segment3"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── in-transit open-PO detail (current recency window) ────────────────────────

_INTRANSIT_COLS = ["item_code", "item_desc", "po_number", "po_date", "vendor_name", "org_name",
                   "procurement_type", "quantity", "received", "cancelled", "in_transit"]


def replace_intransit(crm_rows: list[dict]) -> int:
    data = [(
        str(r.get("Item_Code") or "")[:64], str(r.get("Item_Desc") or "")[:255],
        str(r.get("Po_Number") or "")[:48], _date_or_none(r.get("Po_Date")),
        str(r.get("Vendor_Name") or "")[:255], str(r.get("Org_Name") or "")[:120],
        str(r.get("Procurement_Type") or "")[:64],
        round(_num(r.get("Quantity")), 3), round(_num(r.get("Received")), 3),
        round(_num(r.get("Cancelled")), 3), round(_num(r.get("InTransit")), 3),
    ) for r in (crm_rows or [])]
    return _replace("stg_intransit", _INTRANSIT_COLS, data)


def read_intransit() -> list[dict]:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_desc, po_number, po_date, vendor_name, org_name, "
                            "procurement_type, quantity, received, cancelled, in_transit FROM stg_intransit")
                return [{"Item_Code": r["item_code"], "Item_Desc": r["item_desc"],
                         "Po_Number": r["po_number"], "Po_Date": r["po_date"],
                         "Vendor_Name": r["vendor_name"], "Org_Name": r["org_name"],
                         "Procurement_Type": r["procurement_type"], "Quantity": r["quantity"],
                         "Received": r["received"], "Cancelled": r["cancelled"],
                         "InTransit": r["in_transit"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── dispatch (wide jc0..jcN stored LONG; variant 'jc3' | 'jc13') ──────────────

def replace_dispatch(variant: str, crm_rows: list[dict], n_jc: int) -> int:
    """Explode each wide dispatch row (jc0..jc{n-1}) into LONG rows; store non-zero."""
    data = []
    for r in (crm_rows or []):
        code = str(r.get("ItemCode") or "")[:64]
        name = str(r.get("ItemName") or "")[:255]
        coll = str(r.get("Collector") or "")[:120]
        cid = str(r.get("CollectorId") or "")[:64]
        for i in range(n_jc):
            q = _num(r.get(f"jc{i}"))
            if q:
                data.append((variant, code, name, coll, cid, i, round(q, 3)))
    return _replace("stg_dispatch",
                    ["variant", "item_code", "item_name", "collector", "collector_id", "jc_index", "qty"],
                    data, where="variant=%s", where_params=(variant,))


def read_dispatch(variant: str, n_jc: int) -> list[dict]:
    """Pivot the LONG rows back to the wide dispatch_by_jc() shape
    ({ItemCode, ItemName, Collector, CollectorId, jc0..jc{n-1}})."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT item_code, item_name, collector, collector_id, jc_index, qty "
                            "FROM stg_dispatch WHERE variant=%s", (variant,))
                agg: dict = {}
                for r in cur.fetchall():
                    key = (r["item_code"], r["collector"], r["collector_id"])
                    d = agg.get(key)
                    if d is None:
                        d = {"ItemCode": r["item_code"], "ItemName": r["item_name"],
                             "Collector": r["collector"], "CollectorId": r["collector_id"]}
                        for i in range(n_jc):
                            d[f"jc{i}"] = 0.0
                        agg[key] = d
                    idx = r["jc_index"]
                    if 0 <= idx < n_jc:
                        d[f"jc{idx}"] = float(r["qty"])
                return list(agg.values())
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def read_projection_rows_all(acc_year: str) -> list[dict]:
    """Every staged JC of one accounting year, per item x collector."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT jc, item_name, collector, segment2, segment3, current_q "
                            "FROM stg_projection_rows WHERE acc_year=%s", (acc_year,))
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── demand ledger: projection at customer x item (migrate_demand_ledger.sql) ──

_PROJ_CUST_COLS = ["acc_year", "jc", "customer_id", "customer_name", "collector_id",
                   "collector", "mc_code", "item_code", "item_name",
                   "segment2", "segment3", "segment4",
                   "week1_q", "week2_q", "current_q", "next1_q", "next2_q"]


def replace_projection_customer(acc_year: str, jc: int, crm_rows: list[dict]) -> int:
    """Replace one (acc_year, jc) slice of stg_projection_customer."""
    data = []
    for r in (crm_rows or []):
        name = str(r.get("ItemName") or "").strip()
        if not name:
            continue
        w1, w2 = _num(r.get("Week1Q")), _num(r.get("Week2Q"))
        data.append((
            acc_year, int(jc), _int_or_none(r.get("CustomerId")),
            str(r.get("CustomerName") or "")[:255] or None,
            _int_or_none(r.get("CollectorId")), str(r.get("Collector") or "")[:120] or None,
            str(r.get("McCode") or "")[:32] or None,
            str(r.get("ItemCode") or "")[:64] or None, name[:255],
            str(r.get("Segment2") or "")[:64] or None,
            str(r.get("Segment3") or "")[:64] or None,
            str(r.get("Segment4") or "")[:64] or None,
            round(w1, 3), round(w2, 3), round(w1 + w2, 3),
            round(_num(r.get("Next1Q")), 3), round(_num(r.get("Next2Q")), 3),
        ))
    return _replace("stg_projection_customer", _PROJ_CUST_COLS, data,
                    where="acc_year=%s AND jc=%s", where_params=(acc_year, int(jc)))


def projection_customer_jcs(acc_year: str) -> list[int]:
    """Which JCs of an accounting year are staged (drives the trend chart)."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT jc FROM stg_projection_customer "
                            "WHERE acc_year=%s ORDER BY jc", (acc_year,))
                return [int(r["jc"]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def read_projection_customer(flt: dict, acc_year: str, jc: int) -> list[dict]:
    """Scoped projection lines for one JC at customer x item x collector."""
    where, params = _scope_where(flt or {}, "p")
    where = ["p.acc_year=%s", "p.jc=%s"] + where
    params = [acc_year, int(jc)] + params
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.customer_id, p.customer_name, p.collector_id, p.collector, "
                    "p.mc_code, p.item_code, p.item_name, p.segment2, p.segment3, p.segment4, "
                    "p.week1_q, p.week2_q, p.current_q, p.next1_q, p.next2_q "
                    "FROM stg_projection_customer p WHERE " + " AND ".join(where),
                    tuple(params))
                rows = cur.fetchall()
                for r in rows:
                    for k in ("week1_q", "week2_q", "current_q", "next1_q", "next2_q"):
                        r[k] = float(r[k] or 0)
                return rows
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def _cust_in(customer_ids) -> tuple[str, list]:
    ids = sorted({int(c) for c in (customer_ids or []) if c is not None})
    if not ids:
        return "1=0", []
    return "customer_id IN (" + ",".join(["%s"] * len(ids)) + ")", ids


def ledger_open_soc(customer_ids, jc_from: str, jc_to: str) -> list[dict]:
    """Open committed qty per (customer, item name) for a set of customers, split
    into the qty committed INSIDE the JC window and the qty already overdue when
    that window opens. Firm demand is attributed to the JC its CURRENT commitment
    date falls in — an order promised for October does not protect a September
    projection — and the backlog is reported separately rather than silently
    inflating cover."""
    cin, params = _cust_in(customer_ids)
    if not params:
        return []
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT customer_id, UPPER(TRIM(item_name)) AS item_key, "
                    "       MAX(item_code) AS item_code, "
                    "       SUM(CASE WHEN COALESCE(resched_date, sched_date) BETWEEN %s AND %s "
                    "                THEN balance ELSE 0 END) AS in_jc, "
                    "       SUM(CASE WHEN COALESCE(resched_date, sched_date) < %s "
                    "                THEN balance ELSE 0 END) AS backlog, "
                    "       COUNT(*) AS lines_ "
                    "FROM stg_order_commit WHERE " + cin +
                    " GROUP BY customer_id, UPPER(TRIM(item_name))",
                    tuple([jc_from, jc_to, jc_from] + params))
                rows = cur.fetchall()
                for r in rows:
                    r["in_jc"] = float(r["in_jc"] or 0)
                    r["backlog"] = float(r["backlog"] or 0)
                return rows
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def ledger_dispatch(customer_ids, jc_index) -> list[dict]:
    """Dispatched qty per (customer, item name) inside one JC of the cube."""
    if jc_index is None:
        return []
    cin, params = _cust_in(customer_ids)
    if not params:
        return []
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT customer_id, UPPER(TRIM(item_name)) AS item_key, "
                    "       SUM(qty) AS qty FROM stg_dispatch_scope "
                    "WHERE jc_index=%s AND " + cin +
                    " GROUP BY customer_id, UPPER(TRIM(item_name))",
                    tuple([int(jc_index)] + params))
                rows = cur.fetchall()
                for r in rows:
                    r["qty"] = float(r["qty"] or 0)
                return rows
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── supply competition: firm demand rolled up over the WHOLE order book ───────
#
# The Demand-Protection reads above are always scoped to one persona. The
# competition view also needs the company-wide totals — a sales executive cannot
# see who else committed the stock unless we aggregate outside their scope — so
# ``flt=None`` here means "the whole book" rather than "no access".

def _commit_scope_where(flt) -> tuple[list[str], list]:
    """Scope conditions over stg_order_commit (collector by NAME — the pending
    order feed carries no collector id). flt None/{} = the whole book."""
    where, params = [], []
    if not flt:
        return where, params
    if flt.get("mc_codes"):
        where.append("mc_code IN (" + ",".join(["%s"] * len(flt["mc_codes"])) + ")")
        params += list(flt["mc_codes"])
    if flt.get("collectors"):
        where.append("collector IN (" + ",".join(["%s"] * len(flt["collectors"])) + ")")
        params += list(flt["collectors"])
    if flt.get("collector_ids"):
        # the dashboard filter shape carries ids; the commit table has none, so a
        # caller passing ids gets no extra restriction here (the customer / mc /
        # segment predicates alongside it still apply)
        pass
    if flt.get("customer_ids"):
        where.append("customer_id IN (" + ",".join(["%s"] * len(flt["customer_ids"])) + ")")
        params += [int(c) for c in flt["customer_ids"]]
    if flt.get("segment_grants"):
        ors = []
        for g in flt["segment_grants"]:
            level = g["level"] if g["level"] in _SEG_LEVELS else "segment2"
            cond = f"{level} = %s"
            params.append(g["value"])
            if g.get("collectors"):
                cond += " AND collector IN (" + ",".join(["%s"] * len(g["collectors"])) + ")"
                params += list(g["collectors"])
            ors.append(f"({cond})")
        where.append("(" + " OR ".join(ors) + ")")
    return where, params


def commit_orgs() -> list[dict]:
    """The dispatch orgs that appear on open committed lines — the orgs that
    actually sell, used to decide which stock is available to promise."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT inv_org FROM stg_order_commit "
                            "WHERE inv_org IS NOT NULL AND inv_org <> ''")
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def commit_by_item(flt, stale_cutoff: str) -> list[dict]:
    """Open committed balance per item name. ``flt`` None = the whole company.
    Lines whose commitment date is older than ``stale_cutoff`` are counted in
    ``stale`` and kept OUT of ``balance`` — they are never-closed paperwork, not
    live claims on stock."""
    where, params = _commit_scope_where(flt)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                # Group on the SQUASHED name (the key the ledger joins on), not on
                # UPPER(TRIM(...)): 40 item names differ only by punctuation and
                # would arrive as separate rows, so the caller's dict would keep
                # just one of each and silently drop 2.6M KG of firm demand.
                cur.execute(
                    "SELECT REGEXP_REPLACE(UPPER(item_name), '[^A-Z0-9]', '') AS item_key, "
                    "       MAX(item_name) AS item_name, MAX(item_code) AS item_code, "
                    "       SUM(CASE WHEN COALESCE(resched_date, sched_date) IS NULL "
                    "             OR COALESCE(resched_date, sched_date) >= %s "
                    "            THEN balance ELSE 0 END) AS balance, "
                    "       SUM(CASE WHEN COALESCE(resched_date, sched_date) < %s "
                    "            THEN balance ELSE 0 END) AS stale, "
                    "       COUNT(*) AS lines_, COUNT(DISTINCT customer_id) AS customers "
                    "FROM stg_order_commit" + w +
                    " GROUP BY REGEXP_REPLACE(UPPER(item_name), '[^A-Z0-9]', '')",
                    tuple([stale_cutoff, stale_cutoff] + params))
                rows = cur.fetchall()
                for r in rows:
                    r["balance"] = float(r["balance"] or 0)
                    r["stale"] = float(r["stale"] or 0)
                return rows
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def commit_holders(item_keys, stale_cutoff: str) -> list[dict]:
    """Who holds the live committed balance on these items — one row per
    (item, customer, collector, market circle). Deliberately UNSCOPED: the point
    is to show demand competing from outside the caller's own book. The API layer
    decides which of these may be shown by name."""
    keys = [str(k) for k in (item_keys or []) if k]
    if not keys:
        return []
    # the ledger keys on the squashed name; match on the same shape in SQL by
    # comparing the upper-cased name after stripping non-alphanumerics
    ph = ",".join(["%s"] * len(keys))
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT REGEXP_REPLACE(UPPER(item_name), '[^A-Z0-9]', '') AS item_key, "
                    "       MAX(item_name) AS item_name, customer_id, "
                    "       MAX(customer_name) AS customer_name, collector, mc_code, "
                    "       SUM(balance) AS balance, COUNT(*) AS lines_, "
                    "       MIN(COALESCE(resched_date, sched_date)) AS due "
                    "FROM stg_order_commit "
                    "WHERE (COALESCE(resched_date, sched_date) IS NULL "
                    "       OR COALESCE(resched_date, sched_date) >= %s) "
                    "  AND REGEXP_REPLACE(UPPER(item_name), '[^A-Z0-9]', '') IN (" + ph + ") "
                    "GROUP BY REGEXP_REPLACE(UPPER(item_name), '[^A-Z0-9]', ''), "
                    "         customer_id, collector, mc_code",
                    tuple([stale_cutoff] + keys))
                rows = cur.fetchall()
                for r in rows:
                    r["balance"] = float(r["balance"] or 0)
                    if r.get("due") is not None:
                        r["due"] = str(r["due"])[:10]
                return rows
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── dispatch_scope (permission-dashboard cube, see migrate_dashboard.sql) ─────

_DISP_SCOPE_COLS = ["jc_index", "item_code", "item_name", "customer_id", "customer_name",
                    "collector_id", "collector", "mc_code", "qty", "value_",
                    "segment2", "segment3", "segment4"]


def replace_dispatch_scope(crm_rows: list[dict], n_jc: int) -> int:
    """Explode each wide dispatch_scope row (jc0/val0..jc{n-1}/val{n-1}) into
    LONG rows; keep JCs with any qty or value. Item segments are denormalized
    in from stg_item_segments here (sync time) so dashboard queries stay
    single-table and indexed — sync item_segments before this source."""
    segs = {s["ItemCode"]: s for s in read_item_segments()}
    data = []
    for r in (crm_rows or []):
        code = str(r.get("ItemCode") or "")[:64]
        name = str(r.get("ItemName") or "")[:255]
        cust = _int_or_none(r.get("CustomerId"))
        cname = str(r.get("CustomerName") or "")[:255] or None
        cid = _int_or_none(r.get("CollectorId"))
        coll = str(r.get("Collector") or "")[:120] or None
        mc = str(r.get("McCode") or "")[:32] or None
        s = segs.get(code) or {}
        s2, s3, s4 = s.get("Segment2") or None, s.get("Segment3") or None, s.get("Segment4") or None
        for i in range(n_jc):
            q, v = _num(r.get(f"jc{i}")), _num(r.get(f"val{i}"))
            if q or v:
                data.append((i, code, name, cust, cname, cid, coll, mc,
                             round(q, 3), round(v, 2), s2, s3, s4))
    return _replace("stg_dispatch_scope", _DISP_SCOPE_COLS, data)


_SEG_LEVELS = {"segment2", "segment3", "segment4"}


def _scope_where(flt: dict, a: str = "d") -> tuple[list[str], list]:
    """WHERE conditions + params for a persona scope filter over any table that
    carries the scope columns (mc_code, collector_id, customer_id, segment2-4).
    ``a`` is the table alias — stg_dispatch_scope uses "d", the demand ledger "p".
    See dashboard_datasets for the ``flt`` shapes."""
    where, params = [], []
    if flt.get("mc_codes"):
        where.append(f"{a}.mc_code IN (" + ",".join(["%s"] * len(flt["mc_codes"])) + ")")
        params += list(flt["mc_codes"])
    if flt.get("collector_ids"):
        where.append(f"{a}.collector_id IN (" + ",".join(["%s"] * len(flt["collector_ids"])) + ")")
        params += [int(c) for c in flt["collector_ids"]]
    if flt.get("customer_ids"):
        where.append(f"{a}.customer_id IN (" + ",".join(["%s"] * len(flt["customer_ids"])) + ")")
        params += [int(c) for c in flt["customer_ids"]]
    if flt.get("segment_grants"):
        ors = []
        for g in flt["segment_grants"]:
            level = g["level"] if g["level"] in _SEG_LEVELS else "segment2"
            cond = f"{a}.{level} = %s"
            params.append(g["value"])
            if g.get("collector_ids"):
                cond += f" AND {a}.collector_id IN (" + ",".join(["%s"] * len(g["collector_ids"])) + ")"
                params += [int(c) for c in g["collector_ids"]]
            ors.append(f"({cond})")
        where.append("(" + " OR ".join(ors) + ")")
    return where, params


def _dash_where(flt: dict) -> tuple[list[str], list]:
    """Scope filter over stg_dispatch_scope (alias "d")."""
    return _scope_where(flt, "d")


def dashboard_item_series(flt: dict, item_code: str | None = None,
                          item_name: str | None = None) -> list[dict]:
    """One item's dispatched qty per JC within the persona scope (the click-to-
    drill graph on My Dashboard). Matches by NAME first: the dashboard tables
    group by item_name (an item may carry several codes), so the popup must
    aggregate the same way; code is only the fallback."""
    where, params = _dash_where(flt or {})
    if item_name:
        where.append("UPPER(TRIM(d.item_name)) = %s")
        params.append(str(item_name).strip().upper())
    elif item_code:
        where.append("d.item_code = %s")
        params.append(str(item_code))
    else:
        return []
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT d.jc_index AS jc, SUM(d.qty) AS qty, SUM(d.value_) AS value_ "
                            "FROM stg_dispatch_scope d WHERE " + " AND ".join(where) +
                            " GROUP BY d.jc_index ORDER BY d.jc_index", tuple(params))
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def dashboard_datasets(flt: dict, jc_from: int | None = None) -> dict:
    """All My-Dashboard aggregates in FIVE indexed SQL queries. Aggregation
    stays in MySQL — never haul the 134k-row cube into Python per request
    (that melted the API under concurrent page loads). ``jc_from`` adds a
    per-item sales total over jc_index >= jc_from (the projection-accuracy
    3-JC window) as ``sales3``.

    ``flt`` is one of:
      {}                          -> whole company (Admin)
      {"mc_codes": [...]}         -> Sales Executive (market circles)
      {"collector_ids": [...]}    -> Branch / Regional Manager
      {"customer_ids": [...]}     -> Technical Executive
      {"segment_grants": [{"level": "segment4", "value": v,
                           "collector_ids": [...] | None}, ...]}
                                  -> segment personas (deepest grant level)
    """
    where, params = _dash_where(flt)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    w_cust = w + (" AND " if w else " WHERE ") + "d.customer_id IS NOT NULL"
    base = "FROM stg_dispatch_scope d"
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT d.jc_index AS jc, COALESCE(d.collector, '—') AS collector, "
                            "COALESCE(d.segment3, d.segment2, '—') AS segment, "
                            "SUM(d.qty) AS qty, SUM(d.value_) AS value_ "
                            f"{base}{w} GROUP BY jc, collector, segment", tuple(params))
                cube = cur.fetchall()
                cur.execute("SELECT COUNT(DISTINCT d.customer_id) AS customers, "
                            f"COUNT(DISTINCT d.item_code) AS items {base}{w}", tuple(params))
                totals = cur.fetchone() or {}
                cur.execute("SELECT d.item_code AS code, MAX(d.item_name) AS name, "
                            "SUM(d.qty) AS qty, SUM(d.value_) AS value_ "
                            f"{base}{w} GROUP BY d.item_code ORDER BY SUM(d.qty) DESC LIMIT 15",
                            tuple(params))
                top_items = cur.fetchall()
                cur.execute("SELECT MAX(d.customer_name) AS name, "
                            "SUM(d.qty) AS qty, SUM(d.value_) AS value_ "
                            f"{base}{w_cust} GROUP BY d.customer_id "
                            "ORDER BY SUM(d.qty) DESC LIMIT 15", tuple(params))
                top_customers = cur.fetchall()
                sales3, item_jc = [], []
                if jc_from is not None:
                    w3 = w + (" AND " if w else " WHERE ") + "d.jc_index >= %s"
                    cur.execute("SELECT d.item_name AS name, MAX(d.item_code) AS code, "
                                f"SUM(d.qty) AS qty3 {base}{w3} GROUP BY d.item_name",
                                tuple(params) + (int(jc_from),))
                    sales3 = cur.fetchall()
                    # per item x JC actuals — the projection-accuracy trend needs
                    # per-item variances (WMAPE), not netted totals
                    cur.execute("SELECT d.jc_index AS jc, d.item_name AS name, "
                                "MAX(d.segment2) AS segment2, MAX(d.segment3) AS segment3, "
                                f"SUM(d.qty) AS qty {base}{w} GROUP BY d.jc_index, d.item_name",
                                tuple(params))
                    item_jc = cur.fetchall()
                return {"cube": cube, "totals": totals, "top_items": top_items,
                        "top_customers": top_customers, "sales3": sales3,
                        "item_jc": item_jc}
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return {"cube": [], "totals": {}, "top_items": [], "top_customers": [],
                "sales3": [], "item_jc": []}


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


# ── precomputed plan (Phase 3: heavy build runs in the worker) ────────────────

def save_computed(plan_key: str, obj, n_products: int | None = None) -> None:
    """Store a finished plan (JSON) the worker built, so the API just reads it."""
    try:
        payload = json.dumps(obj, default=str)
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("REPLACE INTO computed_plan (plan_key, payload, n_products, computed_at) "
                            "VALUES (%s,%s,%s,%s)", (plan_key[:48], payload, n_products, datetime.now()))
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        print(f"[compute] save '{plan_key}' failed: {type(e).__name__}: {str(e)[:120]}")


def read_computed(plan_key: str):
    """Return the stored plan dict, or None if the worker hasn't computed it yet."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM computed_plan WHERE plan_key=%s", (plan_key,))
                r = cur.fetchone()
                return json.loads(r["payload"]) if r and r.get("payload") else None
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


def computed_meta(plan_key: str) -> dict | None:
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT n_products, computed_at FROM computed_plan WHERE plan_key=%s", (plan_key,))
                r = cur.fetchone()
                if r and r.get("computed_at") is not None:
                    r["computed_at"] = str(r["computed_at"])
                return r
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


# ── user data-scope (permission dashboard, see migrate_user_scope.sql) ────────

_USER_SCOPE_COLS = ["user_id", "user_name", "username", "email", "persona",
                    "scope_type", "mc_code", "region", "collector_id",
                    "collector_name", "customer_id", "customer_name",
                    "segment2", "segment3", "segment4", "src"]


def replace_user_scope(payload: dict) -> int:
    """Replace stg_user_scope from ``crm_sources.user_scope()`` output.

    Explodes CSV collector lists ('1042,34085') into one row per collector and
    resolves collector names; '0' / '' / NULL collector = all collectors (one
    row with collector_id NULL). De-dupes identical grants."""
    grants = (payload or {}).get("grants") or []
    names = (payload or {}).get("collectors") or {}
    seen, data = set(), []
    for g in grants:
        uid = _int_or_none(g.get("UserId"))
        if not uid:
            continue
        csv = str(g.get("CollectorIds") or "").strip()
        ids = [c for c in (t.strip() for t in csv.split(",")) if c and c != "0"] or [None]
        for cid in ids:
            cid = _int_or_none(cid)
            row = (uid, str(g.get("UserName") or "")[:160] or None,
                   str(g.get("Username") or "")[:80] or None,
                   str(g.get("Email") or "")[:160] or None,
                   str(g.get("Persona") or "")[:32],
                   str(g.get("ScopeType") or "")[:16],
                   str(g.get("McCode") or "")[:32] or None,
                   str(g.get("Region") or "")[:32] or None,
                   cid, (names.get(cid) or None) if cid else None,
                   _int_or_none(g.get("CustomerId")),
                   str(g.get("CustomerName") or "")[:255] or None,
                   str(g.get("Segment2") or "")[:64] or None,
                   str(g.get("Segment3") or "")[:64] or None,
                   str(g.get("Segment4") or "")[:64] or None,
                   str(g.get("Src") or "")[:48])
            key = row[:1] + row[4:9] + row[10:11] + row[12:16]
            if key in seen:
                continue
            seen.add(key)
            data.append(row)
    return _replace("stg_user_scope", _USER_SCOPE_COLS, data)


def read_user_scope(user_id: int | None = None, email: str | None = None,
                    username: str | None = None) -> list[dict]:
    """Grant rows for one user (by CRM user_id, email or username) — or all rows
    when no filter is given. Shape mirrors stg_user_scope columns."""
    where, params = [], []
    if user_id is not None:
        where.append("user_id = %s")
        params.append(int(user_id))
    if email:
        where.append("LOWER(email) = LOWER(%s)")
        params.append(email.strip())
    if username:
        where.append("LOWER(username) = LOWER(%s)")
        params.append(username.strip())
    sql = "SELECT " + ", ".join(_USER_SCOPE_COLS) + " FROM stg_user_scope"
    if where:
        sql += " WHERE " + " OR ".join(where)
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── order commitments (Commitment-Risk page, see migrate_commit.sql) ──────────

_COMMIT_COLS = ["order_no", "soc_line_id", "order_ref", "soc_date", "customer_id",
                "customer_name", "collector", "mc_code", "item_code", "item_name",
                "item_group", "inv_org", "sales_type", "qty", "despatched", "balance",
                "sched_date", "resched_date", "cust_req_date", "resched_reason",
                "wh_comments", "executive", "dispatch_pct",
                "segment2", "segment3", "segment4"]


# CRM stamps EXECUTIVE_NAME as 'No Sales Credit' on 97% of open lines (13,422 of
# 13,804), so the field is a placeholder far more often than a name. Store NULL
# for it rather than showing users a column of "No Sales Credit"; the real owner
# of a line is derived from its collector / market circle via stg_user_scope.
_EXEC_PLACEHOLDER = {"no sales credit", "no sales credit,", "-", "na", "n/a"}


def _exec_name(v) -> str | None:
    name = str(v or "").strip().rstrip(",").strip()
    if not name or name.lower() in _EXEC_PLACEHOLDER:
        return None
    return name[:120]


def replace_order_commit(crm_rows: list[dict]) -> int:
    """Replace stg_order_commit with the open committed lines. The CRM snapshot
    carries its own segments, so nothing needs denormalizing here."""
    data = []
    for r in (crm_rows or []):
        code = str(r.get("ItemCode") or "")[:64]
        data.append((
            _int_or_none(r.get("OrderNo")), _int_or_none(r.get("SocLineId")),
            str(r.get("OrderRef") or "")[:40] or None, _date_or_none(r.get("SocDate")),
            _int_or_none(r.get("CustomerId")), str(r.get("CustomerName") or "").strip()[:255] or None,
            str(r.get("Collector") or "")[:120] or None, str(r.get("McCode") or "")[:32] or None,
            code or None, str(r.get("ItemName") or "")[:255] or None,
            str(r.get("ItemGroup") or "")[:120] or None, str(r.get("InvOrg") or "")[:120] or None,
            str(r.get("SalesType") or "")[:32] or None,
            round(_num(r.get("Qty")), 3), round(_num(r.get("Despatched")), 3),
            round(_num(r.get("Balance")), 3),
            _date_or_none(r.get("SchedDate")), _date_or_none(r.get("ReschedDate")),
            _date_or_none(r.get("CustReqDate")),
            str(r.get("ReschedReason") or "").strip()[:120] or None,
            str(r.get("WhComments") or "").strip()[:255] or None,
            _exec_name(r.get("Executive")),
            round(_num(r.get("DispatchPct")), 2),
            str(r.get("Segment2") or "")[:64] or None,
            str(r.get("Segment3") or "")[:64] or None,
            str(r.get("Segment4") or "")[:64] or None,
        ))
    return _replace("stg_order_commit", _COMMIT_COLS, data)


def _commit_where(flt: dict) -> tuple[list[str], list]:
    """Persona scope over stg_order_commit. Unlike the dispatch cube this table
    keys collectors by NAME (the pending-order feed has no collector id), so the
    collector conditions come from the grants' collector_name column."""
    where, params = [], []
    if flt.get("mc_codes"):
        where.append("mc_code IN (" + ",".join(["%s"] * len(flt["mc_codes"])) + ")")
        params += list(flt["mc_codes"])
    if flt.get("collectors"):
        where.append("collector IN (" + ",".join(["%s"] * len(flt["collectors"])) + ")")
        params += list(flt["collectors"])
    if flt.get("customer_ids"):
        where.append("customer_id IN (" + ",".join(["%s"] * len(flt["customer_ids"])) + ")")
        params += [int(c) for c in flt["customer_ids"]]
    if flt.get("segment_grants"):
        ors = []
        for g in flt["segment_grants"]:
            level = g["level"] if g["level"] in _SEG_LEVELS else "segment2"
            cond = f"{level} = %s"
            params.append(g["value"])
            if g.get("collectors"):
                cond += " AND collector IN (" + ",".join(["%s"] * len(g["collectors"])) + ")"
                params += list(g["collectors"])
            ors.append(f"({cond})")
        where.append("(" + " OR ".join(ors) + ")")
    return where, params


def read_order_commit(flt: dict) -> list[dict]:
    """Every open committed line in the persona's scope (dates as ISO strings)."""
    where, params = _commit_where(flt or {})
    w = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT " + ", ".join(_COMMIT_COLS) + f" FROM stg_order_commit{w}",
                            tuple(params))
                rows = cur.fetchall()
                for r in rows:
                    for k in ("soc_date", "sched_date", "resched_date", "cust_req_date"):
                        if r.get(k) is not None:
                            r[k] = str(r[k])
                    for k in ("qty", "despatched", "balance", "dispatch_pct"):
                        r[k] = float(r[k] or 0)
                return rows
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def read_scope_users() -> list[dict]:
    """One row per (persona, user) in stg_user_scope with their grant count —
    feeds the admin 'View as' switcher on the My Dashboard page."""
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT persona, username, MAX(user_name) AS user_name, "
                            "COUNT(*) AS n_grants FROM stg_user_scope "
                            "WHERE username IS NOT NULL AND username <> '' "
                            "GROUP BY persona, username ORDER BY persona, user_name")
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


# ── saved UI layouts (dashboard card arrangement) ─────────────────────────────
# Reuses the computed_plan key/JSON store under a "ui_layout:" prefix so an
# admin-saved arrangement survives restarts and reaches every user.

def save_ui_layout(key: str, obj) -> None:
    save_computed(f"ui_layout:{key}"[:48], obj)


def read_ui_layout(key: str):
    return read_computed(f"ui_layout:{key}"[:48])


# ── freshness for the UI (data-as-of banner + Refresh now) ────────────────────

SYNC_SOURCES = [
    "item_segments", "stock_lots", "stock_details", "item_business", "pto_pts",
    "stock_aged", "vooki_items", "soc_schedule", "projection", "soc_pending",
    "soc_detail", "intransit", "dispatch_jc3", "dispatch_jc13",
    "projection_rows", "projection_accuracy", "user_scope", "dispatch_scope",
    "order_commit",
]


def sync_status() -> dict:
    """Everything the UI needs: the planning context, per-source freshness, the
    most-recent successful sync, whether anything failed, and pending refreshes."""
    ctx = read_context()
    sources, last_ok, any_error, running = [], None, False, False
    for src in SYNC_SOURCES:
        ls = last_sync(src) or {}
        st = ls.get("status")
        sources.append({"source": src, "status": st, "row_count": ls.get("row_count"),
                        "finished_at": ls.get("finished_at"),
                        "error": (ls.get("error") or "")[:120] or None})
        if st == "error":
            any_error = True
        if st == "running":
            running = True
        fa = ls.get("finished_at")
        if st == "ok" and fa and (last_ok is None or fa > last_ok):
            last_ok = fa
    pending = 0
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM sync_requests WHERE status='pending'")
                pending = (cur.fetchone() or {}).get("c", 0)
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        pass
    return {"context": ctx, "sources": sources, "last_synced": last_ok,
            "any_error": any_error, "syncing": running or pending > 0,
            "pending_requests": pending, "plan": computed_meta("rm_planning")}
