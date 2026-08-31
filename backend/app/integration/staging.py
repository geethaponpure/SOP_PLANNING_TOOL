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


# ── freshness for the UI (data-as-of banner + Refresh now) ────────────────────

SYNC_SOURCES = [
    "item_segments", "stock_lots", "stock_details", "item_business", "pto_pts",
    "stock_aged", "vooki_items", "soc_schedule", "projection", "soc_pending",
    "soc_detail", "intransit", "dispatch_jc3", "dispatch_jc13",
    "projection_rows", "projection_accuracy",
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
