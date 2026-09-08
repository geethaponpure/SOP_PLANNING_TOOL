"""Excel export for the RM Price Impact card.

One sheet with exactly the three columns the card's table shows — finished good,
segment, cost impact — for every impacted product rather than the page the card
has room for. Nothing about the raw materials themselves (name, supplier,
previous and current price, BOM quantity) is written, matching the card: the
purchase-price detail is used to derive the impact and then dropped.

The gate lives in ``rm_impact``: this module is only ever reached with a payload
that already said ``allowed``.
"""
from __future__ import annotations

import io

SECTION_TITLES = {"fgs": "Impact by FG"}

COLUMNS = ("Finished good", "Segment", "Impact %")


def section_rows(payload: dict, _section: str | None = None) -> list[dict]:
    return [{
        "Finished good": r.get("item"),
        "Segment": r.get("segment3") or r.get("segment2") or "",
        "Impact %": r.get("impact_pct"),
    } for r in (payload.get("fgs") or [])]


def _write(ws, rows: list[dict]) -> int:
    if not rows:
        ws.append(["No raw-material rise reaches a product in this scope"])
        return 0
    ws.append(list(COLUMNS))
    for r in rows:
        ws.append([r.get(c) for c in COLUMNS])
    for i, c in enumerate(COLUMNS, start=1):
        width = max(len(str(c)), *(len(str(r.get(c) or "")) for r in rows[:200])) + 2
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(52, max(10, width))
    ws.freeze_panes = "A2"
    return len(rows)


def build(payload: dict, section: str | None = None) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SECTION_TITLES["fgs"]
    _write(ws, section_rows(payload, section))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = ["build", "section_rows", "SECTION_TITLES", "COLUMNS"]
