"""App-owned MySQL store (local) — currently the Vooki FG name -> SKU mapping.

Separate from the read-only CRM (SQL Server): this is the tool's own writable
database. Connection comes from MYSQL_* env vars (see backend/.env). PyMySQL is
imported lazily and every call degrades gracefully (returns an error string
instead of raising) so the rest of the app keeps working when MySQL is not set
up yet — run backend/db/setup.sql once to create the DB, user and table.
"""
from __future__ import annotations

import os
from datetime import datetime


def _config() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "planning_app"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DB", "planning_tool"),
        "connect_timeout": int(os.getenv("MYSQL_TIMEOUT", "5")),
        "charset": "utf8mb4",
        "autocommit": True,
    }


def _connect():
    import pymysql
    from pymysql.cursors import DictCursor
    cfg = _config()
    cfg["cursorclass"] = DictCursor
    return pymysql.connect(**cfg)


SETUP_HINT = ("MySQL not reachable. Run backend/db/setup.sql once "
              "(mysql -u root -p < setup.sql) and check the MYSQL_* keys in backend/.env.")


def _has_col(cur, table: str, col: str) -> bool:
    """True if a column exists — lets saves degrade gracefully before a migration
    (adding the activity/bom_class columns) has been run as root."""
    try:
        cur.execute("SELECT COUNT(*) AS c FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                    (table, col))
        return (cur.fetchone() or {}).get("c", 0) > 0
    except Exception:   # noqa: BLE001
        return False


def status() -> dict:
    """Health probe used by the admin page. {ready: bool, error: str|None}."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return {"ready": True, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ready": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def get_vooki_fg_map() -> dict[str, str]:
    """Return {sku_code: product_name}. Empty dict if the store is unavailable."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sku_code, product_name FROM vooki_fg_map")
                return {r["sku_code"]: r["product_name"] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return {}


def set_vooki_fg_map(sku_code: str, product_name: str) -> dict:
    """Upsert one mapping; an empty product_name removes it. Returns {ok, error}."""
    sku_code = (sku_code or "").strip()
    product_name = (product_name or "").strip()
    if not sku_code:
        return {"ok": False, "error": "sku_code is required"}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                if product_name:
                    cur.execute(
                        "INSERT INTO vooki_fg_map (sku_code, product_name) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE product_name = VALUES(product_name)",
                        (sku_code, product_name))
                else:
                    cur.execute("DELETE FROM vooki_fg_map WHERE sku_code = %s", (sku_code,))
            return {"ok": True, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def get_added_fg_skus() -> list[dict]:
    """Admin-added Vooki FG SKUs: [{sku_code, item_desc}]. Empty on any error
    (e.g. the vooki_fg_sku table not created yet)."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sku_code, item_desc FROM vooki_fg_sku ORDER BY item_desc")
                return [{"sku_code": r["sku_code"], "item_desc": r["item_desc"]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def add_fg_sku(sku_code: str, item_desc: str) -> dict:
    sku_code = (sku_code or "").strip()
    item_desc = (item_desc or "").strip()
    if not sku_code:
        return {"ok": False, "error": "sku_code is required"}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vooki_fg_sku (sku_code, item_desc) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE item_desc = VALUES(item_desc)",
                    (sku_code, item_desc or sku_code))
            return {"ok": True, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def remove_fg_sku(sku_code: str) -> dict:
    sku_code = (sku_code or "").strip()
    if not sku_code:
        return {"ok": False, "error": "sku_code is required"}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vooki_fg_sku WHERE sku_code = %s", (sku_code,))
            return {"ok": True, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def seed_jc_master(rows: list[dict]) -> dict:
    """Upsert the JC calendar (fy, jc_number, start/end/freeze dates)."""
    if not rows:
        return {"ok": True, "error": None}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO JC_MASTER (fy, jc_number, start_date, end_date, freeze_date) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE start_date=VALUES(start_date), "
                    "end_date=VALUES(end_date), freeze_date=VALUES(freeze_date)",
                    [(r["fy"], r["jc_number"], r["start_date"], r["end_date"], r["freeze_date"]) for r in rows])
            return {"ok": True, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def save_jc_plan(fy, jc_number, plan_type, planned_fg_qty, fg_count, rm_rows, note="") -> dict:
    """Save a JC planning run (JC_PLAN header + RM_ALLOCATION_LEDGER details).
    rm_rows: [{rm_code, rm_desc, allocated_qty}]. Returns {ok, plan_id, error}."""
    try:
        conn = _connect()
        try:
            planned_rm = round(sum(float(r.get("allocated_qty", 0) or 0) for r in rm_rows), 2)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO JC_PLAN (plan_datetime, fy, jc_number, plan_type, planned_fg_qty, "
                    "planned_rm_qty, fg_count, rm_count, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (datetime.now(), fy, int(jc_number), plan_type, round(float(planned_fg_qty or 0), 2),
                     planned_rm, int(fg_count or 0), len(rm_rows), note[:255]))
                plan_id = cur.lastrowid
                if rm_rows:
                    if _has_col(cur, "RM_ALLOCATION_LEDGER", "activity"):
                        cur.executemany(
                            "INSERT INTO RM_ALLOCATION_LEDGER (plan_id, rm_code, rm_desc, allocated_qty, plan_type, activity) "
                            "VALUES (%s,%s,%s,%s,%s,%s)",
                            [(plan_id, r.get("rm_code", "")[:64], (r.get("rm_desc") or "")[:255],
                              round(float(r.get("allocated_qty", 0) or 0), 2), plan_type,
                              (r.get("activity") or "")[:20]) for r in rm_rows])
                    else:
                        cur.executemany(
                            "INSERT INTO RM_ALLOCATION_LEDGER (plan_id, rm_code, rm_desc, allocated_qty, plan_type) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            [(plan_id, r.get("rm_code", "")[:64], (r.get("rm_desc") or "")[:255],
                              round(float(r.get("allocated_qty", 0) or 0), 2), plan_type) for r in rm_rows])
            return {"ok": True, "plan_id": plan_id, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "plan_id": None, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def save_plan_fg_demand(plan_id, rows: list[dict]) -> dict:
    """Store per-FG planned demand for audit. rows: [{item_name, current_jc,
    next_jc1, next_jc2, source}]."""
    if not rows:
        return {"ok": True, "written": 0, "error": None}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                has_cls = _has_col(cur, "PLAN_FG_DEMAND", "bom_class")
                has_var = _has_col(cur, "PLAN_FG_DEMAND", "bom_variant")
                cols = "plan_id, item_name, current_jc, next_jc1, next_jc2, source"
                if has_cls:
                    cols += ", bom_class"
                if has_var:
                    cols += ", bom_variant"
                ph = ",".join(["%s"] * (6 + int(has_cls) + int(has_var)))

                def _row(r):
                    base = [int(plan_id), r["item_name"][:255], round(float(r.get("current_jc", 0) or 0), 2),
                            round(float(r.get("next_jc1", 0) or 0), 2), round(float(r.get("next_jc2", 0) or 0), 2),
                            r.get("source", "CRM")[:16]]
                    if has_cls:
                        base.append((r.get("bom_class") or "")[:20])
                    if has_var:
                        base.append((r.get("bom_variant") or "")[:80])
                    return tuple(base)
                cur.executemany(f"INSERT INTO PLAN_FG_DEMAND ({cols}) VALUES ({ph})",
                                [_row(r) for r in rows])
            return {"ok": True, "written": len(rows), "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "written": 0, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def ingest_po_receipts(rows: list[dict]) -> dict:
    """Upsert de-duplicated PO receipt lines into PO_RECEIPTS (unique by
    receipt_no+po+item+lot+subinv+receipt_qty, so overlapping downloads don't
    double-count while genuine split receipt lines under one GRN are preserved).
    rows carry already-parsed values. Degrades gracefully if the table isn't
    created yet (or still has the old coarse key — run migrate_po_receipts_split_lines.sql)."""
    if not rows:
        return {"ok": True, "written": 0, "error": None}
    sql = ("INSERT INTO PO_RECEIPTS (receipt_no, po_number, item_code, item_desc, po_date, "
           "receipt_date, po_qty, receipt_qty, vendor_name, org_name, subinventory, lot_number, "
           "currency, unit_price, ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
           "ON DUPLICATE KEY UPDATE receipt_qty=VALUES(receipt_qty), po_qty=VALUES(po_qty), "
           "receipt_date=VALUES(receipt_date), item_desc=VALUES(item_desc), ingested_at=VALUES(ingested_at)")
    now = datetime.now()

    def _t(r):
        return ((r.get("receipt_no") or "")[:48], (r.get("po_number") or "")[:48],
                (r.get("item_code") or "")[:64], (r.get("item_desc") or "")[:255],
                r.get("po_date"), r.get("receipt_date"),
                round(float(r.get("po_qty") or 0), 3), round(float(r.get("receipt_qty") or 0), 3),
                (r.get("vendor_name") or "")[:255], (r.get("org_name") or "")[:120],
                (r.get("subinventory") or "")[:64], (r.get("lot_number") or "")[:80],
                (r.get("currency") or "")[:8], r.get("unit_price"), now)
    try:
        conn = _connect()
        try:
            data = [_t(r) for r in rows]
            written = 0
            with conn.cursor() as cur:
                for i in range(0, len(data), 2000):
                    cur.executemany(sql, data[i:i + 2000])
                    written += len(data[i:i + 2000])
            return {"ok": True, "written": written, "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "written": 0, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def po_receipts_status() -> dict | None:
    """Row count + latest ingest / receipt date in PO_RECEIPTS (None if unavailable)."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS rows, COUNT(DISTINCT po_number) AS pos, "
                            "MAX(ingested_at) AS last_ingest, MAX(receipt_date) AS latest_receipt "
                            "FROM PO_RECEIPTS")
                r = cur.fetchone() or {}
                for k in ("last_ingest", "latest_receipt"):
                    if r.get(k) is not None:
                        r[k] = str(r[k])
                return r
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


def list_jc_plans(limit: int = 50) -> list[dict]:
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT plan_id, plan_datetime, fy, jc_number, plan_type, planned_fg_qty, "
                    "planned_rm_qty, fg_count, rm_count, note FROM JC_PLAN "
                    "WHERE plan_type LIKE 'JC%%' ORDER BY plan_id DESC LIMIT %s", (int(limit),))
                out = []
                for r in cur.fetchall():
                    r["plan_datetime"] = r["plan_datetime"].isoformat(sep=" ", timespec="minutes") if r["plan_datetime"] else ""
                    for k in ("planned_fg_qty", "planned_rm_qty"):
                        r[k] = float(r[k]) if r[k] is not None else 0.0
                    out.append(r)
                return out
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def get_vessel_mapping() -> list[dict]:
    """All rows from vessel_product_mapping (vessel/equipment constraints per
    product). Empty on error (table not created)."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM vessel_product_mapping")
                return list(cur.fetchall())
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def get_jc_plan(plan_id) -> dict | None:
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT plan_id, plan_type, fy, jc_number, plan_datetime, note "
                            "FROM JC_PLAN WHERE plan_id=%s", (int(plan_id),))
                return cur.fetchone()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None


def get_plan_fg_demand(plan_id) -> list[dict]:
    """Per-FG demand rows for a plan: [{item_name, current_jc, next_jc1, next_jc2, source}]."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                bc = _has_col(cur, "PLAN_FG_DEMAND", "bom_class")
                bv = _has_col(cur, "PLAN_FG_DEMAND", "bom_variant")
                extra = (", bom_class" if bc else "") + (", bom_variant" if bv else "")
                cur.execute(f"SELECT item_name, current_jc, next_jc1, next_jc2, source{extra} "
                            f"FROM PLAN_FG_DEMAND WHERE plan_id=%s", (int(plan_id),))
                return [{"item_name": r["item_name"], "current_jc": float(r["current_jc"] or 0),
                         "next_jc1": float(r["next_jc1"] or 0), "next_jc2": float(r["next_jc2"] or 0),
                         "source": r["source"], "bom_class": r.get("bom_class") if bc else None,
                         "bom_variant": r.get("bom_variant") if bv else None}
                        for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def get_plan_allocations(plan_id) -> list[dict]:
    """RM allocation ledger rows for a plan: [{rm_code, rm_desc, allocated_qty}]."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                act = _has_col(cur, "RM_ALLOCATION_LEDGER", "activity")
                cur.execute(f"SELECT rm_code, rm_desc, allocated_qty{', activity' if act else ''} "
                            f"FROM RM_ALLOCATION_LEDGER WHERE plan_id=%s", (int(plan_id),))
                return [{"rm_code": r["rm_code"], "rm_desc": r["rm_desc"],
                         "allocated_qty": float(r["allocated_qty"] or 0),
                         "activity": r.get("activity") if act else None} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return []


def save_adhoc_evaluations(plan_id, fy, jc_number, rows: list[dict]) -> dict:
    """Log per-item adhoc evaluations. rows: [{item_name, projected_qty,
    pending_soc_qty, order_qty, adhoc_qty, status}]."""
    if not rows:
        return {"ok": True, "written": 0, "error": None}
    try:
        conn = _connect()
        try:
            now = datetime.now()
            pid = int(plan_id) if plan_id else None
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO ADHOC_EVALUATION (eval_datetime, plan_id, fy, jc_number, item_name, "
                    "projected_qty, pending_soc_qty, order_qty, adhoc_qty, status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(now, pid, fy, int(jc_number) if jc_number else None, r["item_name"][:255],
                      round(float(r.get("projected_qty", 0) or 0), 2), round(float(r.get("pending_soc_qty", 0) or 0), 2),
                      round(float(r.get("order_qty", 0) or 0), 2), round(float(r.get("adhoc_qty", 0) or 0), 2),
                      r.get("status", "")[:16]) for r in rows])
            return {"ok": True, "written": len(rows), "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "written": 0, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}


def bulk_set_vooki_fg_map(pairs: list[dict]) -> dict:
    """Upsert many {sku_code, product_name} rows in one transaction."""
    rows = [(p.get("sku_code", "").strip(), (p.get("product_name") or "").strip())
            for p in pairs if p.get("sku_code")]
    if not rows:
        return {"ok": True, "written": 0, "error": None}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                to_set = [(s, n) for s, n in rows if n]
                to_del = [(s,) for s, n in rows if not n]
                if to_set:
                    cur.executemany(
                        "INSERT INTO vooki_fg_map (sku_code, product_name) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE product_name = VALUES(product_name)", to_set)
                if to_del:
                    cur.executemany("DELETE FROM vooki_fg_map WHERE sku_code = %s", to_del)
            return {"ok": True, "written": len(rows), "error": None}
        finally:
            conn.close()
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "written": 0, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:180]}"}
