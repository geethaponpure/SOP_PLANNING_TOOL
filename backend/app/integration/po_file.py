"""PO-receipts loader (Pure_PO_Receipts_Register ... .csv).

Derives per-item procurement intelligence used to build the RM master:
unit cost, lead time + lead-time variability (receipt date - PO date), supplier
count (distinct vendors), and a typical order size proxy for MOQ.

The CSV has a 2-line title banner before the real header row.
"""
from __future__ import annotations

import csv
import statistics
from datetime import datetime


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _date(v):
    if not v:
        return None
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt)
        except ValueError:
            continue
    return None


def _find_header(rows: list[list[str]]) -> int:
    for i, r in enumerate(rows[:10]):
        if "Item Code" in r and "Po Date" in r:
            return i
    return 0


def load_po(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    hi = _find_header(rows)
    hdr = rows[hi]
    ix = {n: i for i, n in enumerate(hdr)}

    def c(row, name):
        i = ix.get(name)
        return row[i] if i is not None and i < len(row) else None

    agg: dict[str, dict] = {}
    for row in rows[hi + 1:]:
        code = c(row, "Item Code")
        if not code:
            continue
        a = agg.setdefault(code, {"prices": [], "leads": [], "qtys": [],
                                  "vendors": set(), "term_days": [],
                                  "uom": c(row, "UOM"), "division": c(row, "Division"),
                                  "last_receipt": None})
        price = _num(c(row, "Po Unit Price"))
        if price > 0:
            a["prices"].append(price)
        q = _num(c(row, "Po Quantity")) or _num(c(row, "Receipt Qty"))
        if q > 0:
            a["qtys"].append(q)
        v = c(row, "Vendor No") or c(row, "Vendor Name")
        if v:
            a["vendors"].add(str(v).strip())
        pod, rcd = _date(c(row, "Po Date")), _date(c(row, "Receipt Date"))
        if pod and rcd:
            lead = (rcd - pod).days
            if 0 <= lead <= 365:
                a["leads"].append(lead)
        td = _num(c(row, "Po Term Days"))
        if td > 0:
            a["term_days"].append(td)
        if rcd and (a["last_receipt"] is None or rcd > a["last_receipt"]):
            a["last_receipt"] = rcd

    out: dict[str, dict] = {}
    for code, a in agg.items():
        leads = a["leads"] or a["term_days"]
        lead = round(statistics.median(leads), 1) if leads else 21.0
        lead_var = round((statistics.pstdev(leads) / lead), 2) if len(leads) > 1 and lead else 0.25
        out[code] = {
            "unit_cost": round(statistics.mean(a["prices"]), 2) if a["prices"] else 0.0,
            "lead_time_days": lead,
            "lead_time_variability": min(lead_var, 1.0),
            "suppliers": max(1, len(a["vendors"])),
            "moq": round(statistics.median(a["qtys"]), 0) if a["qtys"] else 100.0,
            "receipts": len(a["qtys"]),
            "last_receipt": a["last_receipt"].date().isoformat() if a["last_receipt"] else None,
            "uom": a["uom"], "division": a["division"],
        }
    return out
