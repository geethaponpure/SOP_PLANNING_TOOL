"""
Purchase Price Variance (PPV) engine.

Standard price = weighted-average purchase price across FY 2025-2026 (the base year).
Each subsequent JC's actual weighted purchase price is compared to the standard.
Variance = (actual_wap - standard_wap) × qty → negative is FAVOURABLE (bought cheaper).

Output:
  - per-item standard price, min/max, volatility, JC breakdown
  - JC-wise aggregate performance (total spend, PPV ₹ and %)
  - summary scorecard metrics
  
"""


from __future__ import annotations

import os
import statistics
from collections import defaultdict
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────

def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0

def _to_fy(date_val) -> str | None:
    """Return 'YYYY-YYYY' fiscal year for a date (Apr-Mar Indian FY)."""
    if date_val is None:
        return None
    try:
        if hasattr(date_val, "month"):
            y, m = date_val.year, date_val.month
        else:
            s = str(date_val)[:10]
            parts = s.replace("/", "-").split("-")
            y, m = int(parts[0]), int(parts[1])
        fy_start = y if m >= 4 else y - 1
        return f"{fy_start}-{fy_start + 1}"
    except Exception:   # noqa: BLE001
        return None


def _to_jc(date_val, jc_map: dict | None = None) -> int | None:
    """Map a date to a JC number using the JC calendar (fallback: month-based)."""
    if date_val is None:
        return None
    if jc_map:
        try:
            if hasattr(date_val, "date"):
                d = date_val.date()
            elif hasattr(date_val, "year"):
                d = date_val
            else:
                from datetime import date as _date
                parts = str(date_val)[:10].replace("/", "-").split("-")
                d = _date(int(parts[0]), int(parts[1]), int(parts[2]))
            for jc_num, (start, end) in jc_map.items():
                if start <= d <= end:
                    return jc_num
        except Exception:   # noqa: BLE001
            pass
    # fallback: approximate JC from month (13 JCs, ~4 weeks each; JC1 starts April)
    try:
        if hasattr(date_val, "month"):
            y, m = date_val.year, date_val.month
        else:
            parts = str(date_val)[:10].replace("/", "-").split("-")
            y, m = int(parts[0]), int(parts[1])
        # fiscal month (Apr=1 … Mar=13)
        fm = ((m - 4) % 12) + 1
        jc = ((fm - 1) // 1) + 1   # one-month ≈ one-JC for fallback
        return min(max(jc, 1), 13)
    except Exception:   # noqa: BLE001
        return None


def _load_jc_map() -> dict | None:
    """Try to load the JC calendar; return None if unavailable."""
    try:
        from ..integration import jc_calendar
        cal = jc_calendar.calendar()
        return {j["jc"]: (j["start"], j["end"]) for j in cal}
    except Exception:   # noqa: BLE001
        return None


# ── PO-receipts loader ────────────────────────────────────────────────────────

def _load_po_receipts(warnings: list[str]) -> list[dict]:
    """Load PO receipts from available files (multi-year FY workbooks or CSV)."""
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[4]   # sop-planning-tool/

    search_dirs = [
        os.getenv("PO_RECEIPTS_DIR", ""),
        r"z:\PO_receipts",
        r"\\10.1.0.17\PPCAIProjects\PO_receipts",
        str(root),
    ]
    po_files: list[str] = []
    seen: set[str] = set()
    for d in search_dirs:
        if not d:
            continue
        try:
            p = _Path(d)
            if not p.is_dir():
                continue
            for pat in ("Fy-*.xlsx", "FY-*.xlsx", "*PO_Receipts*.xlsx", "*PO_Receipts*.csv"):
                for f in sorted(p.glob(pat)):
                    key = f.name.lower()
                    if key not in seen:
                        seen.add(key)
                        po_files.append(str(f))
        except OSError:
            continue

    if not po_files:
        csv = os.getenv("PO_CSV", "")
        if not csv:
            for pat in ("Pure_PO_Receipts*.csv", "Pure_PO_Receipts*.xls*"):
                for f in sorted(root.glob(pat)):
                    csv = str(f)
                    break
        if csv:
            po_files = [csv]

    if not po_files:
        warnings.append("No PO-receipts files found; PPV uses synthetic data.")
        return []

    rows: list[dict] = []
    for fpath in po_files:
        try:
            from ..integration import po_file as _po
            r = _po.load_po_receipts(fpath)
            rows.extend(r)
        except Exception as e:   # noqa: BLE001
            warnings.append(f"po_file {Path(fpath).name}: {type(e).__name__}: {str(e)[:120]}")
    return rows


# ── main entry point ──────────────────────────────────────────────────────────

STD_FY = os.getenv("PPV_STD_FY", "2025-2026")   # baseline FY for standard price

# Candidate column names in PO-receipts rows
_ITEM_COLS   = ["Item_Code", "ItemCode", "item_code", "Itemcode", "Material", "Code"]
_NAME_COLS   = ["Item_Name", "ItemName", "Description", "item_description"]
_DATE_COLS   = ["GRN_Date", "receipt_date", "PO_Date", "ReceiptDate", "Date", "trx_date"]
_QTY_COLS    = ["Received_Qty", "received_qty", "GRN_Qty", "Quantity", "Qty"]
_PRICE_COLS  = ["Unit_Price", "unit_price", "Price", "Rate", "UnitCost", "BasicPrice", "basic_price"]
_CURR_COLS   = ["Currency", "currency", "Curr"]
_VENDOR_COLS = ["Vendor", "vendor_name", "Supplier", "SupplierName", "VendorName"]


def _pick(row: dict, cols: list[str]):
    for c in cols:
        if c in row:
            return row[c]
    return None


def build_ppv(data: dict) -> dict:
    """Return the PPV scorecard payload."""
    warnings: list[str] = []
    jc_map   = _load_jc_map()
    source   = os.getenv("DATA_SOURCE", "synthetic").lower()

    if source == "live":
        po_rows = _load_po_receipts(warnings)
        if not po_rows:
            po_rows = _synthetic_po(data, warnings)
            warnings.append("Falling back to synthetic PO data for PPV.")
    else:
        po_rows = _synthetic_po(data, warnings)

    if not po_rows:
        return {"note": "No PO-receipts data available for PPV calculation."}

    # ── aggregate into buckets: (item_code, fy, jc) → {spend, qty} ───────────
    std_fy = STD_FY   # e.g. "2025-2026"

    BucketKey = tuple   # (item_code, fy, jc)
    bucket_spend: dict[BucketKey, float] = defaultdict(float)
    bucket_qty:   dict[BucketKey, float] = defaultdict(float)
    item_names:   dict[str, str]          = {}

    for r in po_rows:
        code  = _pick(r, _ITEM_COLS)
        if not code:
            continue
        code  = str(code).strip()
        qty   = _num(_pick(r, _QTY_COLS))
        price = _num(_pick(r, _PRICE_COLS))
        if qty <= 0 or price <= 0:
            continue
        date_val = _pick(r, _DATE_COLS)
        fy   = _to_fy(date_val) or std_fy
        jc   = _to_jc(date_val, jc_map) or 1
        name = _pick(r, _NAME_COLS) or code
        item_names[code] = str(name)
        bucket_spend[(code, fy, jc)] += qty * price
        bucket_qty  [(code, fy, jc)] += qty

    # ── standard: weighted-average price per item in std_fy ───────────────────
    std_price:  dict[str, float] = {}
    std_spend:  dict[str, float] = {}
    std_qty:    dict[str, float] = {}
    for (code, fy, jc), spend in bucket_spend.items():
        if fy == std_fy:
            std_spend[code] = std_spend.get(code, 0.0) + spend
            std_qty[code]   = std_qty.get(code, 0.0) + bucket_qty[(code, fy, jc)]
    for code, spend in std_spend.items():
        q = std_qty[code]
        if q > 0:
            std_price[code] = round(spend / q, 4)

    if not std_price:
        return {"note": f"No PO receipts found for FY {std_fy}. "
                         "Adjust PPV_STD_FY env var or provide PO data."}

    # ── per-JC aggregate performance (all items with a standard) ─────────────
    jc_spend:   dict[int, float] = defaultdict(float)
    jc_qty:     dict[int, float] = defaultdict(float)
    jc_std_cost: dict[int, float] = defaultdict(float)   # what we should have paid

    # collect all non-std-FY JCs present
    active_jcs: set[int] = set()
    for (code, fy, jc) in bucket_spend:
        if fy != std_fy and code in std_price:
            active_jcs.add(jc)
            spend = bucket_spend[(code, fy, jc)]
            qty   = bucket_qty  [(code, fy, jc)]
            jc_spend[jc]    += spend
            jc_qty[jc]      += qty
            jc_std_cost[jc] += qty * std_price[code]

    # if no "non-std" JCs (data is only std_fy), use std_fy JCs as comparison base
    if not active_jcs:
        for (code, fy, jc) in bucket_spend:
            if code in std_price:
                active_jcs.add(jc)
                spend = bucket_spend[(code, fy, jc)]
                qty   = bucket_qty  [(code, fy, jc)]
                jc_spend[jc]    += spend
                jc_qty[jc]      += qty
                jc_std_cost[jc] += qty * std_price[code]
        warnings.append("All PO data is within the standard FY; JC performance compares to own WAP.")

    jc_perf = []
    for jc in sorted(active_jcs):
        spend    = jc_spend.get(jc, 0.0)
        qty      = jc_qty.get(jc, 0.0)
        std_cost = jc_std_cost.get(jc, 0.0)
        ppv      = round(spend - std_cost, 0)
        ppv_pct  = round((ppv / std_cost * 100) if std_cost > 0 else 0.0, 1)
        jc_perf.append({
            "jc":      jc,
            "qty":     round(qty, 1),
            "spend":   round(spend, 0),
            "std_cost": round(std_cost, 0),
            "ppv":     ppv,
            "ppv_pct": ppv_pct,
        })

    best_jc  = min(jc_perf, key=lambda j: j["ppv"])["jc"] if jc_perf else None
    worst_jc = max(jc_perf, key=lambda j: j["ppv"])["jc"] if jc_perf else None

    # ── per-item detail ────────────────────────────────────────────────────────
    items = []
    for code, sp in std_price.items():
        # gather all actual prices per JC (not limited to std_fy)
        jc_prices: dict[int, float] = {}
        jc_spends: dict[int, float] = {}
        for (c, fy, jc), spend in bucket_spend.items():
            if c == code:
                qty = bucket_qty[(c, fy, jc)]
                if qty > 0:
                    jc_prices[jc] = round(spend / qty, 4)
                    jc_spends[jc] = round(spend, 0)

        all_prices = list(jc_prices.values())
        min_p = round(min(all_prices), 4) if all_prices else sp
        max_p = round(max(all_prices), 4) if all_prices else sp
        vol   = round((max_p - min_p) / sp * 100 if sp > 0 else 0.0, 1)

        jcs_above = sum(1 for p in all_prices if p > sp)
        jcs_below = sum(1 for p in all_prices if p <= sp)

        # timing overspend: total extra paid vs standard in unfavourable JCs
        timing_os = 0.0
        worst_jc_item = None
        worst_ppv = 0.0
        for (c, fy, jc), spend in bucket_spend.items():
            if c == code:
                qty = bucket_qty[(c, fy, jc)]
                ppv_item = spend - qty * sp
                if ppv_item > worst_ppv:
                    worst_ppv = ppv_item
                    worst_jc_item = jc
                if ppv_item > 0:
                    timing_os += ppv_item

        total_spend = sum(bucket_spend[(code, fy, jc)]
                          for (c, fy, jc) in bucket_spend if c == code)

        items.append({
            "code":             code,
            "name":             item_names.get(code, code),
            "std_price":        sp,
            "min_price":        min_p,
            "max_price":        max_p,
            "volatility_pct":   vol,
            "jcs_above":        jcs_above,
            "jcs_below":        jcs_below,
            "timing_overspend": round(timing_os, 0),
            "worst_jc":         worst_jc_item,
            "spend":            round(total_spend, 0),
            "jc_prices":        jc_prices,
        })

    items.sort(key=lambda i: -i["timing_overspend"])

    total_spend_all = sum(i["spend"] for i in items)
    total_overspend = sum(j["ppv"] for j in jc_perf if j["ppv"] > 0)
    pct_os = round(total_overspend / total_spend_all * 100 if total_spend_all > 0 else 0.0, 1)

    return {
        "std_fy":        std_fy,
        "jc_performance": jc_perf,
        "items":          items,
        "_warnings":      warnings,
        "summary": {
            "std_items":           len(std_price),
            "total_spend":         round(total_spend_all, 0),
            "timing_overspend":    round(total_overspend, 0),
            "timing_overspend_pct": pct_os,
            "best_jc":             best_jc,
            "worst_jc":            worst_jc,
            "note":                "; ".join(warnings) if warnings else None,
        },
    }


# ── synthetic PO data ─────────────────────────────────────────────────────────

def _synthetic_po(data: dict, warnings: list[str]) -> list[dict]:
    """Generate realistic synthetic PO rows for PPV demo."""
    import random
    from datetime import date, timedelta

    rng = random.Random(20260101)
    rows = []
    # build 2 years of monthly PO receipts
    start = date(2024, 4, 1)
    rms = data.get("rms", {})
    if not rms:
        warnings.append("No RM master available for synthetic PO generation.")
        return []

    for rm_code, rm in rms.items():
        base_price = _num(rm.get("unit_cost", 1.0))
        if base_price <= 0:
            base_price = rng.uniform(1.0, 50.0)
        moq = _num(rm.get("moq", 1000))

        for month in range(24):
            d = start + timedelta(days=month * 28 + rng.randint(0, 10))
            # price volatility ±20 %
            price = round(base_price * rng.uniform(0.82, 1.22), 4)
            qty   = round(moq * rng.uniform(0.5, 2.0), 1)
            rows.append({
                "Item_Code":    rm_code,
                "Item_Name":    rm.get("name", rm_code),
                "GRN_Date":     d,
                "Received_Qty": qty,
                "Unit_Price":   price,
                "Vendor":       f"Supplier-{rng.randint(1, 5)}",
            })
    return rows