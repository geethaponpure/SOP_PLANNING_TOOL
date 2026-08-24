"""Lot-wise stock loader (Oracle 'Stock - lot wise' extract).

Reads the lot-level inventory workbook the client provided (Sample_Data.xlsx)
and aggregates it to per-item on-hand, cost, value, earliest expiry, shelf life
and slow-moving (>1yr) stock. Column names match the Oracle staging extract, so
the same mapping applies when that live feed is wired -- only the source swaps.

Pointed at via the STOCK_XLSX env var; used by the adapter to fill the netting
position and expiry/E&O signals on real inventory.
"""
from __future__ import annotations

from datetime import datetime

# header -> logical field (exact names from the client workbook)
COLS = {
    "code": "Item Code", "uom": "UOM", "division": "Division",
    "cost": "Cost", "qty": "Qty", "value": "Value", "stock_days": "Stock Days",
    "grn": "GRN Date", "expiry": "Expiry Date", "subinv": "Sub Inv",
    "org": "Organization",
}
# age buckets (qty) older than one year -> slow-moving / E&O candidates
SLOW_BUCKETS = ["Q366to730", "Q730"]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_date(v):
    return v if isinstance(v, datetime) else None


def load_stock_xlsx(path: str) -> dict[str, dict]:
    """Aggregate lot rows to one record per item code."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = list(next(rows))
    ix = {name: i for i, name in enumerate(header)}

    def cell(row, key):
        col = COLS.get(key, key)
        return row[ix[col]] if col in ix else None

    agg: dict[str, dict] = {}
    for row in rows:
        code = cell(row, "code")
        if not code:
            continue
        a = agg.setdefault(code, {
            "on_hand": 0.0, "value": 0.0, "lots": 0, "slow_qty": 0.0,
            "expiry": None, "grn": None, "no_sale": False, "stock_days": None,
            "uom": cell(row, "uom"), "division": cell(row, "division"),
            "subinv": cell(row, "subinv"),
        })
        qty = _num(cell(row, "qty"))
        a["on_hand"] += qty
        a["value"] += _num(cell(row, "value"))
        a["lots"] += 1
        for b in SLOW_BUCKETS:
            if b in ix:
                a["slow_qty"] += _num(row[ix[b]])
        exp = _as_date(cell(row, "expiry"))
        if exp and (a["expiry"] is None or exp < a["expiry"]):
            a["expiry"] = exp        # earliest expiry across lots (FEFO)
        grn = _as_date(cell(row, "grn"))
        if grn and (a["grn"] is None or grn < a["grn"]):
            a["grn"] = grn
        sd = cell(row, "stock_days")
        if isinstance(sd, str) and "no sale" in sd.lower():
            a["no_sale"] = True
        elif isinstance(sd, (int, float)):
            a["stock_days"] = max(a["stock_days"] or 0, sd)

    # finalise derived fields
    out: dict[str, dict] = {}
    for code, a in agg.items():
        unit_cost = round(a["value"] / a["on_hand"], 2) if a["on_hand"] else 0.0
        shelf_life_days = None
        if a["expiry"] and a["grn"]:
            shelf_life_days = max(1, (a["expiry"] - a["grn"]).days)
        out[code] = {
            "on_hand": round(a["on_hand"], 2),
            "inventory_value": round(a["value"], 2),
            "unit_cost": unit_cost,
            "lots": a["lots"],
            "slow_moving_qty": round(a["slow_qty"], 2),
            "expiry_date": a["expiry"].date().isoformat() if a["expiry"] else None,
            "shelf_life_days": shelf_life_days,
            "no_sale": a["no_sale"],
            "stock_days": a["stock_days"],
            "uom": a["uom"], "division": a["division"], "subinv": a["subinv"],
        }
    return out
