"""Which finished goods My Dashboard reports on: the ones we make or repack.

Pure Chemical also trades bulk solvents — TOLUENE, METHANOL, ACETIC ACID, ISO
PROPYL ALCOHOL, MIXED XYLENE — and by weight that book dwarfs everything made
in-house: over 13 JCs it is 428 of 452 million KG dispatched, 94.8%, and 94.2%
of the value. My Dashboard is about the manufactured business, so every card on
it counts only items whose BOM classes them Manufacturing or Repack/Relabel.

The status is not ours to invent: every BOM variant in the production workbook
already carries ``bom_class`` from ``planning_filter.classify_bom``, the same
status the MSL page shows as Manufacturing / Repack-Relabel / Trading. An item
with no BOM at all is traded; ``internal`` and ``unclassified`` builds are R&D
and data gaps, and are excluded with it.

The dashboard's SQL cannot join a workbook, so this module resolves the workbook
once into the set of ``stg_dispatch_scope.item_code`` values that pass, cached
against both the workbook's timestamp and the dispatch sync stamp.
"""
from __future__ import annotations

import os

from ..integration import mysql_db
from ..integration import planning_filter as _pf
from ..integration import staging

# BOM classes that count as "we make or repack this"
ACTIVITY = ("manufacturing", "repack_relabel")

_MAP: dict = {}
_CODES: dict = {}


def _stamp() -> str:
    from .live import _resolve_file
    path = _resolve_file("PLANNING_BOM_XLSX")
    try:
        return f"{path}|{os.path.getmtime(path):.0f}"
    except OSError:
        return path or ""


def activity_map() -> dict:
    """{squashed assembly name: 'manufacturing' | 'repack_relabel'}.

    Manufacturing wins when an item carries both classes.
    """
    st = _stamp()
    if _MAP.get("stamp") == st and _MAP.get("map") is not None:
        return _MAP["map"]
    from .live import _resolve_file
    out: dict = {}
    try:
        raw = _pf.load_bom_detailed(_resolve_file("PLANNING_BOM_XLSX"))
    except Exception:   # noqa: BLE001
        return _MAP.get("map") or {}
    for k, variants in raw["by_squash"].items():
        classes = {v.get("bom_class") for v in variants}
        for cls in ACTIVITY:
            if cls in classes:
                out[k] = cls
                break
    _MAP.update({"stamp": st, "map": out})
    return out


def is_allowed(name) -> bool:
    """Does this item name belong to the made-or-repacked book?"""
    return _pf._squash(name) in activity_map()


def allowed_item_codes() -> list[str] | None:
    """The ``stg_dispatch_scope.item_code`` values that pass, so the dashboard's
    aggregates can filter in SQL. ``None`` means the workbook could not be read —
    the caller then reports the full book rather than an empty page, because an
    unreadable file must not be mistaken for "nothing qualifies".
    """
    amap = activity_map()
    if not amap:
        return None
    st = f"{_stamp()}|{(staging.last_sync('dispatch_scope') or {}).get('finished_at') or ''}"
    if _CODES.get("stamp") == st and _CODES.get("codes") is not None:
        return _CODES["codes"]
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT item_code, item_name FROM stg_dispatch_scope")
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        return None
    codes = sorted({str(r["item_code"]) for r in rows
                    if r.get("item_code") and _pf._squash(r.get("item_name")) in amap})
    _CODES.update({"stamp": st, "codes": codes})
    return codes


__all__ = ["ACTIVITY", "activity_map", "is_allowed", "allowed_item_codes"]
