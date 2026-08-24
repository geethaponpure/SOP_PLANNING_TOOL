"""Finished-goods shelf-life loader (FG_Shelf_Life_days_QMS workbook).

Per item code -> shelf life in days (from QMS). Used to set real shelf-life caps
on PTS holding and expiry-risk scoring (Sections 10.4 / 12), overriding the
stock-derived expiry and the default where present.
"""
from __future__ import annotations

COLS = {"code": "Itemcode", "days": "SHELF_LIFE_DAYS"}


def load_shelf_life(path: str) -> dict[str, int]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rit = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rit)]
    try:
        ci, di = header.index(COLS["code"]), header.index(COLS["days"])
    except ValueError:
        return {}
    out: dict[str, int] = {}
    for r in rit:
        if ci >= len(r) or di >= len(r):
            continue
        code, days = r[ci], r[di]
        if code and isinstance(days, (int, float)) and days > 0:
            out[str(code).strip()] = int(days)
    return out
