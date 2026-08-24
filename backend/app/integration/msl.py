"""MSL (Minimum Stock Level) for finished products.

MSL = 50% of the average one-JC dispatch over the latest 13 JCs (one year). The window
slides forward automatically as each JC completes. Per item we also surface the average
qty/JC, the movement frequency (how many of the 13 JCs had dispatch), and the customer
coverage (unique collectors served).

Pure computation + snapshot storage live here; the CRM dispatch pull and BOM/activity
lookup are wired in main.py. Snapshots persist to MySQL (``sc_msl_snapshot`` /
``sc_msl_item``) with a JSON fallback (msl_store.json) — same pattern as user_master.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path

from . import jc_calendar as _jc
from . import mysql_db
from . import planning_filter as _pf

_JSON = os.getenv("MSL_STORE") or str(Path(__file__).resolve().parents[2] / "msl_store.json")
_LOCK = threading.RLock()
N_JCS = 13
# MSL is only meaningful for broadly-moving items — keep items served by MORE than this
# many unique customers, AND that moved in MORE than MIN_FREQ of the 13 JCs.
MIN_CUSTOMERS = int(os.getenv("MSL_MIN_CUSTOMERS", "5"))
MIN_FREQ = int(os.getenv("MSL_MIN_FREQ", "10"))


def stock_by_code(stock_rows, warehouse_orgs, excluded_subinv) -> dict:
    """Current on-hand FG stock per item_code, split Warehouse (org in warehouse_orgs)
    vs Branch (everywhere else). Excluded sub-inventories are dropped."""
    warehouse = {str(o) for o in (warehouse_orgs or [])}
    excl = {str(x).lower() for x in (excluded_subinv or [])}
    out: dict = {}
    for r in stock_rows or []:
        if _pf._norm(r.get("SubInv")).lower() in excl:
            continue
        code = _pf._norm(r.get("ItemCode"))
        qty = _pf._num(r.get("Qty"))
        if not code or qty == 0:
            continue
        a = out.setdefault(code, {"warehouse": 0.0, "branch": 0.0})
        if _pf._norm(r.get("Organization")) in warehouse:
            a["warehouse"] += qty
        else:
            a["branch"] += qty
    return out


# ── the sliding 13-JC window + its reference label ────────────────────────────
def jc_window(n: int = N_JCS, today: date | None = None) -> list[dict]:
    """The latest ``n`` JC entries ending at the current JC (chronological)."""
    allj = sorted(_jc._all_jcs(), key=lambda j: j["start"])
    cur = _jc.current_jc_entry(today)
    if cur:
        idx = next((i for i, j in enumerate(allj)
                    if j["fy"] == cur["fy"] and j["jc"] == cur["jc"]), len(allj) - 1)
    else:
        idx = len(allj) - 1
    return allj[max(0, idx - n + 1): idx + 1]


def window_meta(window: list[dict]) -> dict:
    cur = window[-1] if window else {}
    fy_start = (cur.get("fy") or "0-0").split("-")[0]
    ref = f"msl_jc{cur.get('jc', 0)}_{fy_start}"
    return {"reference": ref, "jc_label": cur.get("label", ""), "fy": cur.get("fy", ""),
            "n_jcs": len(window), "jc_from": window[0]["from"] if window else "",
            "jc_to": cur.get("to", ""),
            "jcs": [{"fy": j["fy"], "label": j["label"], "from": j["from"], "to": j["to"]} for j in window]}


# ── activity classification (Manufacturing / Repack-Relabel / Trading) ────────
def activity_maps(bom_path: str) -> tuple[dict, dict]:
    """Return (by_item_code, by_squashed_desc) -> activity, derived from the BOM file.
    Manufacturing if it has a manufacturing recipe, Repack/Relabel if a repack BOM,
    else the item has no BOM and is treated as Trading."""
    by_code: dict[str, set] = {}
    by_desc: dict[str, set] = {}
    if bom_path:
        idx = _pf.load_bom_detailed(bom_path)
        for variants in idx["by_desc"].values():
            for v in variants:
                by_code.setdefault(v["assembly_item"], set()).add(v.get("bom_class"))
                by_desc.setdefault(_pf._squash(v["assembly_desc"]), set()).add(v.get("bom_class"))
    return by_code, by_desc


def _classify(classes: set) -> str:
    if "manufacturing" in classes:
        return "Manufacturing"
    if "repack_relabel" in classes:
        return "Repack/Relabel"
    if classes:
        return "Other"
    return "Trading"


def item_activity(code, desc, by_code, by_desc) -> str:
    classes = by_code.get(_pf._norm(code)) or by_desc.get(_pf._squash(desc)) or set()
    return _classify(classes)


# ── aggregate dispatch rows -> per-item MSL ───────────────────────────────────
def aggregate(dispatch_rows: list[dict], window: list[dict], by_code, by_desc,
              business_map: dict | None = None, stock_map: dict | None = None,
              min_customers: int = MIN_CUSTOMERS, min_freq: int = MIN_FREQ) -> list[dict]:
    n = len(window) or 1
    business_map = business_map or {}
    stock_map = stock_map or {}
    agg: dict[str, dict] = {}
    # MSL is arrived per finished-product NAME — the same product may carry several item
    # codes; we roll them all up under one name (dispatch qty summed, customers unioned,
    # on-hand stock summed across every code sharing the name).
    for r in dispatch_rows:
        name = _pf._norm(r.get("ItemName"))
        code = _pf._norm(r.get("ItemCode"))
        key = _pf._squash(name) or code
        if not key:
            continue
        a = agg.setdefault(key, {"name": name, "codes": set(), "code_qty": {},
                                 "jc": [0.0] * n, "cols": set(), "total": 0.0})
        if not a["name"]:
            a["name"] = name
        if code:
            a["codes"].add(code)
        rq = 0.0
        for i in range(n):
            q = _pf._num(r.get(f"jc{i}"))
            a["jc"][i] += q
            a["total"] += q
            rq += q
        if code:
            a["code_qty"][code] = a["code_qty"].get(code, 0.0) + rq
        col = r.get("CollectorId") or r.get("Collector")
        if col:
            a["cols"].add(str(col))
    out = []
    for key, a in agg.items():
        total = a["total"]
        if total <= 0:
            continue
        if len(a["cols"]) <= min_customers:      # MSL only for items with > min_customers
            continue
        freq = sum(1 for q in a["jc"] if q > 0)
        if freq <= min_freq:                     # ...and that moved in > min_freq of the JCs
            continue
        avg_jc = total / n
        codes = sorted(a["codes"])
        wh = round(sum(stock_map.get(c, {}).get("warehouse", 0.0) for c in codes), 1)
        br = round(sum(stock_map.get(c, {}).get("branch", 0.0) for c in codes), 1)
        # Dominant activity: the name is labelled by whichever activity its codes carry the
        # most dispatch volume in. Ties break by priority (Mfg > Repack > Other > Trading).
        _pr = {"Manufacturing": 3, "Repack/Relabel": 2, "Other": 1, "Trading": 0}
        vol: dict = {}
        for c, cq in a["code_qty"].items():
            cls = _classify(by_code.get(c) or by_desc.get(key, set()))
            vol[cls] = vol.get(cls, 0.0) + cq
        activity = max(vol, key=lambda k: (vol[k], _pr.get(k, 0))) if vol else "Trading"
        business = next((_pf._norm(business_map.get(c, "")) for c in codes if business_map.get(c)), "")
        out.append({
            "item_code": codes[0] if codes else "",   # representative code (kept for storage only)
            "item_codes": codes, "code_count": len(codes),
            "item_name": a["name"],
            "activity": activity,
            "business": business,
            "avg_qty_per_jc": round(avg_jc, 1),
            "freq_jcs": freq, "freq_pct": round(freq / n * 100),
            "customer_coverage": len(a["cols"]),
            "total_qty": round(total, 1),
            "msl": round(0.5 * avg_jc, 1),
            "warehouse_stock": wh, "branch_stock": br, "onhand_stock": round(wh + br, 1),
            "jc_qty": [round(q, 1) for q in a["jc"]],
        })
    out.sort(key=lambda x: -x["avg_qty_per_jc"])
    return out


# ── snapshot storage (MySQL + JSON fallback) ──────────────────────────────────
_ready = {"ok": False}


def _db_ready(force=False) -> bool:
    if _ready["ok"] and not force:
        return True
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables WHERE "
                            "table_schema=DATABASE() AND table_name IN ('sc_msl_snapshot','sc_msl_item')")
                _ready["ok"] = (cur.fetchone() or {}).get("c", 0) >= 2
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        _ready["ok"] = False
    return _ready["ok"]


def _json_load() -> dict:
    with _LOCK:
        try:
            with open(_JSON, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("snapshots", {})
            return d
        except (FileNotFoundError, json.JSONDecodeError):
            return {"snapshots": {}}


def _json_save(d) -> None:
    with _LOCK:
        tmp = _JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, default=str)
        os.replace(tmp, _JSON)


def save_snapshot(meta: dict, rows: list[dict], actor="") -> dict:
    ref = meta["reference"]
    header = {**meta, "created_by": actor or "",
              "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
              "n_items": len(rows)}
    if _db_ready():
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sc_msl_item WHERE reference=%s", (ref,))
                cur.execute("DELETE FROM sc_msl_snapshot WHERE reference=%s", (ref,))
                cur.execute(
                    "INSERT INTO sc_msl_snapshot (reference, jc_label, fy, n_jcs, jc_from, jc_to, "
                    "n_items, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (ref[:60], meta.get("jc_label", "")[:20], meta.get("fy", "")[:20], meta.get("n_jcs", 0),
                     meta.get("jc_from", ""), meta.get("jc_to", ""), len(rows), (actor or "")[:120], datetime.now()))
                for r in rows:
                    cur.execute(
                        "INSERT INTO sc_msl_item (reference, item_code, item_name, activity, business, "
                        "avg_qty_per_jc, freq_jcs, customer_coverage, total_qty, msl, "
                        "warehouse_stock, branch_stock, onhand_stock) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (ref[:60], r["item_code"][:40], (r["item_name"] or "")[:200], r["activity"][:24],
                         (r.get("business") or "")[:60], r["avg_qty_per_jc"], r["freq_jcs"],
                         r["customer_coverage"], r["total_qty"], r["msl"],
                         r.get("warehouse_stock", 0), r.get("branch_stock", 0), r.get("onhand_stock", 0)))
        finally:
            conn.close()
    else:
        with _LOCK:
            d = _json_load()
            d["snapshots"][ref] = {"header": header, "rows": rows}
            _json_save(d)
    return {"ok": True, "reference": ref, "n_items": len(rows)}


def list_snapshots() -> list[dict]:
    if _db_ready():
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT reference, jc_label, fy, n_jcs, jc_from, jc_to, n_items, "
                            "created_by, created_at FROM sc_msl_snapshot ORDER BY created_at DESC")
                rows = cur.fetchall()
                for r in rows:
                    if r.get("created_at") is not None:
                        r["created_at"] = str(r["created_at"])
                return list(rows)
        finally:
            conn.close()
    heads = [s["header"] for s in _json_load().get("snapshots", {}).values()]
    return sorted(heads, key=lambda h: h.get("created_at", ""), reverse=True)


def get_snapshot(reference: str) -> dict | None:
    if _db_ready():
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM sc_msl_snapshot WHERE reference=%s", (reference,))
                head = cur.fetchone()
                if not head:
                    return None
                if head.get("created_at") is not None:
                    head["created_at"] = str(head["created_at"])
                cur.execute("SELECT item_code, item_name, activity, business, avg_qty_per_jc, freq_jcs, "
                            "customer_coverage, total_qty, msl, warehouse_stock, branch_stock, onhand_stock "
                            "FROM sc_msl_item WHERE reference=%s ORDER BY avg_qty_per_jc DESC", (reference,))
                return {"header": head, "rows": list(cur.fetchall())}
        finally:
            conn.close()
    return _json_load().get("snapshots", {}).get(reference)


def storage_info() -> dict:
    ready = _db_ready(force=True)
    return {"backend": "mysql" if ready else "json", "db_ready": ready,
            "migration": "backend/db/migrate_msl.sql",
            "json_snapshots": len(_json_load().get("snapshots", {}))}
