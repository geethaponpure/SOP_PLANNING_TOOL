"""Adapter: real CRM / Oracle rows -> the engine ``data`` dict.

Design: the planning engine consumes one ``data`` shape (see ``data.py``). This
module builds that shape from live sources. Where the client confirmed column
names (PTO/PTS query) the mapping is concrete; everywhere else we match against
a list of candidate column names via ``pick()``, which raises a clear,
actionable error naming the available columns if none match.

Engineered for the real-world conditions the probe revealed:
  - SCALE: the catalogue is ~14k items. Scope with CRM_DIVISION (one Division),
    ACTIVE_ONLY (items with recent dispatch) and MAX_SKUS (top-by-volume cap) so
    the pilot is fast and focused (blueprint Phase 1).
  - PARTIAL FAILURE: a source that errors (e.g. SOC permission denied) is caught,
    recorded as a load warning, and planning continues on what loaded -- it does
    not crash the whole cycle.
  - PHASING: LIVE_DEMAND_ONLY=yes runs the demand side on real CRM data while
    RM/BOM/capacity stay as placeholders until Oracle staging is wired.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from . import crm_sources
from ..engine import baseline as _bl

# Project root (…/sop-planning-tool) -- where the supply files live.
_ROOT = Path(__file__).resolve().parents[3]

# Default data files (the client's exports). Glob patterns so timestamped names
# resolve automatically. Used when the matching env var is unset, so the tool
# "just works". Sample_Data.xlsx is deliberately excluded (it is only a tiny
# sample; Pure_Stock_Agings is the real stock source).
_DEFAULT_FILES = {
    "STOCK_XLSX": ["Pure_Stock_Agings*.xlsx"],
    "BOM_XLSX": ["BOM_Planning_Tool*.xlsx"],
    # Supply & RM planning page uses the richer BOM Extract (has BOM_TYPE +
    # BOM_CREATION_DATE for the packing split and creation-date preference).
    "PLANNING_BOM_XLSX": ["BOM Extract*.xlsx", "BOM_Planning_Tool*.xlsx"],
    "PO_CSV": ["Pure_PO_Receipts*.csv"],
    "BUSINESS_PLAN_XLSX": ["PCBusinessPlan*.xlsx"],
    "CYCLE_TIME_XLSX": ["*Cycle Time*.xlsx", "*Cycle_Time*.xlsx"],
    "SHELF_LIFE_XLSX": ["FG_Shelf_Life*.xlsx"],
    # Vooki planning: SKU master to unpack packaged FG stock into units / KG-Lit.
    "VOOKI_MASTER_CSV": ["Vooki_Item_Master_Clean.csv", "Vooki_Item_Master*.csv"],
}


def _resolve_file(env_var: str) -> str:
    """Env var path if set, else the latest matching file in the project root."""
    val = os.getenv(env_var, "").strip()
    if val:
        return val
    for pattern in _DEFAULT_FILES.get(env_var, []):
        matches = sorted(_ROOT.glob(pattern))
        if matches:
            return str(matches[-1])     # latest by name (newest timestamp)
    return ""


# Directories holding the multi-year PO receipts (the FY workbooks the client
# dropped into PO_receipts). Searched in order; the project root is the fallback.
_PO_DIRS = [
    os.getenv("PO_RECEIPTS_DIR", "").strip(),
    str(_ROOT / "PO_receipts"),
    r"z:\PO_receipts",
    r"\\10.1.0.17\PPCAIProjects\PO_receipts",
    str(_ROOT),
]


def _register_date(fname: str):
    """Parse the DDMMYY date embedded in a Pure_PO_Receipts_Register_<DDMMYY> name."""
    import re
    from datetime import date
    m = re.search(r"(\d{2})(\d{2})(\d{2})(?=\.[^.]+$)", fname)
    if m:
        dd, mm, yy = (int(x) for x in m.groups())
        try:
            return date(2000 + yy, mm, dd)
        except ValueError:
            return None
    return None


def resolve_latest_po_register() -> str:
    """Newest 'Pure_PO_Receipts_Register_<DDMMYY>' snapshot for pending/in-transit —
    chosen by the filename date (falls back to file mtime), since the DDMMYY names
    do NOT sort chronologically (e.g. 150726 < 300626 lexically but is newer)."""
    from datetime import date
    best, best_key = "", (date.min, -1.0)
    seen = set()
    for d in _PO_DIRS:
        if not d:
            continue
        try:
            p = Path(d)
            if not p.is_dir():
                continue
            for pat in ("*PO_Receipts*Register*.xls", "*PO_Receipts*Register*.xlsx",
                        "*PO_Receipts*Register*.htm", "*PO_Receipts*Register*.html",
                        "*PO_Receipts*Register*.csv"):
                for f in p.glob(pat):
                    if f.name.lower() in seen:
                        continue
                    seen.add(f.name.lower())
                    key = (_register_date(f.name) or date.min, f.stat().st_mtime)
                    if key > best_key:
                        best, best_key = str(f), key
        except OSError:
            continue
    return best or _resolve_file("PO_CSV")


def _resolve_po_files() -> list[str]:
    """All PO-receipts files (the 2-year FY workbooks), de-duped by filename so a
    local copy wins over its network twin. Falls back to the single CSV export."""
    out, seen = [], set()
    for d in _PO_DIRS:
        if not d:
            continue
        try:
            p = Path(d)
            if not p.is_dir():
                continue
            for pat in ("Fy-*.xlsx", "FY-*.xlsx", "*PO_Receipts*.xlsx",
                        "*PO_Receipts*.xls", "*PO_Receipts*.htm", "*PO_Receipts*.html",
                        "*PO_Receipts*.csv"):
                for f in sorted(p.glob(pat)):
                    key = f.name.lower()
                    if key not in seen:
                        seen.add(key)
                        out.append(str(f))
        except OSError:
            continue
    if out:
        return out
    csv = _resolve_file("PO_CSV")
    return [csv] if csv else []


# ---- candidate column names per logical field (edit after the probe) --------
COLUMN_MAP = {
    "item_code":   ["Item_Code", "ItemCode", "item_code", "Code"],
    "item_name":   ["Item_Name", "ItemName", "item_description", "Description"],
    "uom":         ["UOM", "Uom", "uom"],
    # dispatch history (confirmed from probe: Itemcode / trx_date / sale_quantity)
    "disp_item":   ["Itemcode", "Item_Code", "ItemCode", "item_code"],
    "disp_date":   ["trx_date", "schedule_date", "invoice_date", "creation_date"],
    "disp_qty":    ["sale_quantity", "schedule_quantity", "Quantity", "Qty"],
    "disp_price":  ["unit_price", "basic_price", "UnitPrice"],
    # projection / business plan (TODO: confirm when SP is provided)
    "plan_item":   ["Item_Code", "ItemCode", "item_code"],
    "plan_period": ["Period", "Month", "PlanMonth", "YearMonth"],
    "plan_qty":    ["PlanQty", "ProjectionQty", "BusinessPlanQty", "Quantity", "Qty"],
    # SOC pending (TODO: confirm once GRANT EXECUTE on SP_SOCSummaryReport lands)
    "soc_item":    ["Item_Code", "ItemCode", "item_code", "Itemcode"],
    "soc_qty":     ["PendingQty", "BalanceQty", "SOCQty", "PendingQuantity",
                    "Quantity", "Qty"],
    # quotes (confirmed from probe: item_code / quantity)
    "quote_item":  ["item_code", "Item_Code", "ItemCode"],
    "quote_qty":   ["quantity", "Quantity", "Qty", "QuotedQty"],
}


def pick(row: dict, key: str):
    """Return the value for the first candidate column present in the row."""
    for col in COLUMN_MAP[key]:
        if col in row:
            return row[col]
    raise KeyError(
        f"None of {COLUMN_MAP[key]} found for '{key}'. "
        f"Available columns: {list(row.keys())}. "
        f"Update COLUMN_MAP['{key}'] in adapter.py."
    )


def pick_safe(row: dict, key: str, default=None):
    try:
        return pick(row, key)
    except KeyError:
        return default


# ---------------------------------------------------------------- helpers
def _month_labels(end: date, count: int) -> list[str]:
    labels, y, m = [], end.year, end.month
    for _ in range(count):
        labels.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(labels))


def _to_period(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "year"):
        return f"{value.year:04d}-{value.month:02d}"
    s = str(value)[:7].replace("/", "-")
    return s if len(s) == 7 and s[4] == "-" else None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe(fn, name: str, warnings: list[str]) -> list[dict]:
    """Run a source loader; on failure record a warning and return []. This is
    what keeps a single permission/SQL error (e.g. SOC) from killing the cycle."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        msg = f"{name}: {type(e).__name__}: {str(e).splitlines()[0][:160]}"
        warnings.append(msg)
        print(f"[adapter] source unavailable -> {msg}")
        return []


# ---------------------------------------------------------------- builders
def _build_item_master(pto_rows: list[dict]) -> dict[str, dict]:
    """SKU master + PTO/PTS flag (columns confirmed by client)."""
    family_seg = os.getenv("CRM_FAMILY_SEGMENT", "Segment3")  # Segment3 = product family
    skus: dict[str, dict] = {}
    for r in pto_rows:
        code = r.get("Item_Code")
        if not code:
            continue
        skus[code] = {
            "sku": code,
            "name": r.get("Item_Name", code),
            "family": (r.get(family_seg) or r.get("Segment3")
                       or r.get("Segment2") or "Unclassified"),
            "division": r.get("Segment2") or "—",
            "uom": r.get("UOM", "KG"),
            "crm_pto_pts": (r.get("Itemtype") or "PTO").upper(),
            "segments": {f"Segment{i}": r.get(f"Segment{i}") for i in range(1, 5)},
        }
    return skus


def _build_history(dispatch_rows: list[dict], periods: list[str],
                   sku_ids: set[str]) -> dict[str, list[dict]]:
    pset = set(periods)
    buckets: dict[str, dict[str, float]] = {s: {p: 0.0 for p in periods} for s in sku_ids}
    for r in dispatch_rows:
        code = pick_safe(r, "disp_item")
        if code not in buckets:
            continue
        period = _to_period(pick_safe(r, "disp_date"))
        if period not in pset:
            continue
        buckets[code][period] += _num(pick_safe(r, "disp_qty", 0))
    return {
        s: [{"period": p, "shipped": round(buckets[s][p], 1),
             "true_demand": round(buckets[s][p], 1), "event": None} for p in periods]
        for s in sku_ids
    }


def _index_qty(rows: list[dict], item_key: str, qty_key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        code = pick_safe(r, item_key)
        if code is None:
            continue
        out[code] = out.get(code, 0.0) + _num(pick_safe(r, qty_key, 0))
    return out


def _price_map(dispatch_rows: list[dict]) -> dict[str, float]:
    """Average dispatched unit price per item -> real unit value for ABC."""
    agg: dict[str, list[float]] = {}
    for r in dispatch_rows:
        code = pick_safe(r, "disp_item")
        if code is None:
            continue
        p = _num(pick_safe(r, "disp_price", 0))
        if p <= 0:
            continue
        a = agg.setdefault(code, [0.0, 0.0])
        a[0] += p
        a[1] += 1
    return {c: round(v[0] / v[1], 2) for c, v in agg.items() if v[1]}


# ---------------------------------------------------------------- entry point
def build_live_dataset() -> dict:
    """Assemble the engine dataset from live CRM (and, later, Oracle) feeds."""
    warnings: list[str] = []
    active_only = os.getenv("ACTIVE_ONLY", "yes").lower() in ("yes", "true", "1")
    max_skus = int(os.getenv("MAX_SKUS", "300"))
    history_months = int(os.getenv("HISTORY_MONTHS", "24"))

    today = date.today()
    cycle_period = f"{today.year:04d}-{today.month:02d}"
    end = date(today.year - (today.month == 1),
               12 if today.month == 1 else today.month - 1, 1)
    periods = _month_labels(end, history_months)

    # ---- CRM demand side (real, fault-tolerant) ----
    all_skus = _build_item_master(_safe(crm_sources.pto_pts, "pto_pts", warnings))
    dispatch = _safe(crm_sources.dispatch_details, "dispatch_details", warnings)
    history_all = _build_history(dispatch, periods, set(all_skus))
    price_map = _price_map(dispatch)   # real unit value per item -> value-based ABC

    # --- scope to a manageable, meaningful pilot set ---
    def total_vol(sid: str) -> float:
        return sum(h["shipped"] for h in history_all.get(sid, []))

    candidates = list(all_skus)
    if active_only:
        candidates = [s for s in candidates if total_vol(s) > 0]
    candidates.sort(key=total_vol, reverse=True)
    if max_skus > 0:
        candidates = candidates[:max_skus]
    sku_ids = set(candidates)
    skus = {s: all_skus[s] for s in sku_ids}
    history = {s: history_all[s] for s in sku_ids}
    if not sku_ids:
        warnings.append("No items in scope after filtering (check CRM_DIVISION / "
                        "ACTIVE_ONLY / dispatch history).")

    # ---- customer service-tier classification (read-only A/B/C/D/E) ----
    classification: dict[str, str] = {}
    for c in _safe(crm_sources.customer_classification, "customer_classification", warnings):
        num = c.get("CustomerNumber")
        cls = c.get("Class")
        if num is not None and cls:
            classification[str(num).strip()] = str(cls).strip()[0]  # 'A Class' -> 'A'
    if classification:
        warnings.append(f"customer_classification: {len(classification)} customers tiered (A/B/C/D/E).")

    # ---- business plan / projection (from the JC-wise report export) ----
    # Keyed by ItemName (the report has no item code); ~99.8% match CRM names.
    bp_by_name: dict[str, dict] = {}
    bp_path = _resolve_file("BUSINESS_PLAN_XLSX")
    if bp_path:
        def _load_bp():
            from . import bp_file
            return bp_file.load_business_plan(bp_path, accyear=os.getenv("BP_ACCYEAR") or None,
                                              classification=classification or None)
        try:
            bp_by_name = _load_bp()
            matched = sum(1 for s in skus.values()
                          if str(s.get("name", "")).strip().upper() in bp_by_name)
            warnings.append(f"business_plan: loaded {len(bp_by_name)} items from "
                            f"{os.path.basename(bp_path)} ({matched} match items in scope).")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"business_plan: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
    else:
        warnings.append("business_plan: no PCBusinessPlan*.xlsx found.")

    soc = _index_qty(_safe(crm_sources.soc_pending, "soc_pending", warnings), "soc_item", "soc_qty")
    quotes = _index_qty(_safe(crm_sources.quote_details, "quote_details", warnings), "quote_item", "quote_qty")

    # ---- lot-wise stock (from the stock-agings file) ----
    stock: dict[str, dict] = {}
    stock_path = _resolve_file("STOCK_XLSX")
    if stock_path:
        def _load_stock():
            from . import stock_file
            return stock_file.load_stock_xlsx(stock_path)
        try:
            stock = _load_stock()
            matched = len(set(stock) & sku_ids)
            warnings.append(f"stock_file: loaded {len(stock)} items from {os.path.basename(stock_path)} "
                            f"({matched} match items in scope).")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"stock_file: {type(e).__name__}: {str(e).splitlines()[0][:160]}")

    # ---- finished-goods shelf life (QMS) ----
    shelf_map: dict[str, int] = {}
    sl_path = _resolve_file("SHELF_LIFE_XLSX")
    if sl_path:
        try:
            from . import shelf_file
            shelf_map = shelf_file.load_shelf_life(sl_path)
            matched = len(set(shelf_map) & sku_ids)
            warnings.append(f"shelf_life: {len(shelf_map)} items from {os.path.basename(sl_path)} "
                            f"({matched} match items in scope).")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"shelf_life: {type(e).__name__}: {str(e).splitlines()[0][:160]}")

    # ---- cycle-time / capacity (batch sizes + equipment for RCCP) ----
    cycle_by_product: dict[str, dict] = {}
    cycle_assets: dict[str, dict] = {}
    ct_path = _resolve_file("CYCLE_TIME_XLSX")
    if ct_path:
        try:
            from . import capacity_file
            ct = capacity_file.load_cycle_time(ct_path)
            cycle_by_product, cycle_assets = ct["by_product"], ct["assets"]
            matched = sum(1 for s in skus.values()
                          if str(s.get("name", "")).strip().upper() in cycle_by_product)
            warnings.append(f"cycle_time: {len(cycle_by_product)} products, {len(cycle_assets)} "
                            f"equipment ({matched} match items in scope).")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"cycle_time: {type(e).__name__}: {str(e).splitlines()[0][:160]}")

    cycle_month = int(cycle_period.split("-")[1])
    for code, sku in skus.items():
        recent = [h["shipped"] for h in history[code][-3:]]
        recent_avg = round(sum(recent) / len(recent), 1) if recent else 0.0
        st = stock.get(code, {})
        # real unit value: prefer dispatch sales price, else stock unit cost
        unit_value = price_map.get(code) or st.get("unit_cost") or 1.0

        # seasonal factor for the cycle month from this item's own history,
        # used to disaggregate the ANNUAL business plan to a MONTHLY projection.
        series = [h["shipped"] for h in history[code]]
        seas = _bl.seasonal_indices(series, periods)
        factor = seas[cycle_month - 1] if seas else 1.0

        ct = cycle_by_product.get(str(sku.get("name", "")).strip().upper(), {})
        bp = bp_by_name.get(str(sku.get("name", "")).strip().upper())
        # monthly = annual/12 * seasonal index (indices average 1 -> 12 months sum to annual)
        projection_monthly = round(bp["annual_budget"] / 12.0 * factor, 1) if bp and bp["annual_budget"] > 0 else None
        # LMS signal: the report's own LMS potential (annual), seasonalized; else quotes
        lms_val = (round(bp["lms_potential"] / 12.0 * factor, 1)
                   if bp and bp.get("lms_potential", 0) > 0
                   else round(quotes.get(code, recent_avg), 1))
        sku.update({
            "owner": sku.get("owner", "Sales"),
            "region": sku.get("division", "—"),
            "unit_value": unit_value,
            # shelf life: QMS file -> stock expiry-derived -> default
            "shelf_life_days": shelf_map.get(code) or st.get("shelf_life_days") or 540,
            "hazard": sku.get("hazard", "None"),
            "production_lead_time_days": sku.get("production_lead_time_days", 10),
            "quality_release_days": 7,
            "batch_size": int(ct["max_batch"]) if ct.get("max_batch") else int(os.getenv("DEFAULT_BATCH", "100")),
            "equipment": ct.get("equipment"),
            "cycle_time_per_batch": ct.get("cycle_time_hrs"),
            "run_rate_kg_hr": ct.get("run_rate_kg_hr"),
            "co_product": None,
            "pattern": "live",
            "projection": projection_monthly,                # business plan (annual, seasonalized to month)
            "annual_budget": round(bp["annual_budget"], 1) if bp else None,
            "seasonal_factor": round(factor, 3),
            "customer_tier": (bp.get("top_tier") if bp else None),          # dominant customer class
            "key_customer_share": (bp.get("key_customer_share") if bp else None),  # A+B budget share
            "customer_tier_mix": (bp.get("class_budget") if bp else None),
            "pending_soc": round(soc.get(code, 0.0), 1),     # firm floor (0 if SOC source down)
            "lms": lms_val,                                  # report LMS potential (seasonalized) or quotes
            "on_hand": round(st.get("on_hand", 0.0), 1),     # lot-wise stock (real if loaded)
            "in_transit": 0.0, "allocated": 0.0,
            "inventory_value": st.get("inventory_value", 0.0),
            "expiry_date": st.get("expiry_date"),
            "slow_moving_qty": st.get("slow_moving_qty", 0.0),
            "no_sale": st.get("no_sale", False),
            "stock_days": st.get("stock_days"),
            "recent_avg": recent_avg,
            "dq_defects": [],
        })

    # ---- supply side: from the Excel/CSV files (stock + BOM + PO receipts) ----
    bom_path = _resolve_file("BOM_XLSX")
    po_path = _resolve_file("PO_CSV")
    supply_from_files = bool(bom_path or po_path)
    if supply_from_files:
        from . import files_supply
        fs = files_supply.build_file_supply(sku_ids, stock, bom_path or None,
                                            po_path or None, warnings)
        rms, bom = fs["rms"], fs["bom"]
        assets, family_rate = fs["assets"], fs["family_rate"]
        supply_placeholder = False
    else:
        from ..data import _build_synthetic_dataset
        synth = _build_synthetic_dataset()
        rms, bom = synth["rms"], {}
        assets, family_rate = synth["assets"], synth["family_rate"]
        supply_placeholder = True
        warnings.append("No BOM_XLSX / PO_CSV set: RM/BOM/capacity are placeholders.")

    # equipment-based capacity (from the cycle-time file) replaces the empty
    # placeholder assets so RCCP can run against real bottleneck equipment.
    if cycle_assets:
        assets = cycle_assets
        family_rate = {}

    # mark make vs buy: manufactured items have a BOM; traded items legitimately
    # do not (so the DQ gate must not flag them for "missing BOM").
    for code, sku in skus.items():
        sku["is_manufactured"] = code in bom
        sku["make_or_buy"] = "make" if code in bom else "buy"

    families = {s["family"] for s in skus.values()}
    default_batch = int(os.getenv("DEFAULT_BATCH", "100"))
    for w in warnings:
        print(f"[adapter] warning: {w}")
    return {
        "cycle_period": cycle_period,
        "history_periods": periods,
        "skus": skus,
        "history": history,
        "rms": rms,
        "bom": bom,
        "assets": assets,
        "family_rate": {**{f: 8.0 for f in families}, **(family_rate or {})},
        "family_region": {f: "—" for f in families},
        "family_batch": {f: default_batch for f in families},
        "co_products": {},
        "sales_owners": {},
        "_source": "live-crm" + ("+files" if supply_from_files else ""),
        "_supply_placeholder": supply_placeholder,  # True -> DQ skips RM/BOM checks
        "_scope": {"division": os.getenv("CRM_DIVISION", "(all)"),
                   "active_only": active_only, "max_skus": max_skus,
                   "items_in_scope": len(sku_ids), "items_total": len(all_skus)},
        "_load_warnings": warnings,
    }


