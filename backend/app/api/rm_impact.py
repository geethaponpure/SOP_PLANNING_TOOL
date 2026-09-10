"""RM Price Impact on FG — what a raw-material price rise does to finished goods.

    RM price change -> RM cost per FG -> FG cost impact -> exposure -> margin

WHO CAN SEE IT
--------------
Restricted to **Division Head and Business Head** (and Admin, who backs the
View-as switcher). The FG and revenue half of this card scopes cleanly through
the normal permission model, but the RM half is supplier and purchase-price
information, which the model says nothing about — so the whole card is gated
rather than half-shown. Everyone else gets ``allowed: False`` and the page draws
nothing.

HOW THE IMPACT IS CALCULATED
----------------------------
Per the brief's section 2 — quantity, not percentage:

    added cost per FG unit = SUM over components( BOM qty x (new price - old price) )
    FG cost impact %       = added cost per unit / the FG's own unit cost
    exposure per cycle     = added cost per unit x recent dispatch volume
    margin erosion         = added cost per unit / realised selling price

The BOM quantity comes from the **production BOM** already loaded for the JC
plan, not from CRM: ``PurchaseRequisationRawMaterial.quantity_per_assembly`` is
NULL on all 9,977 rows, and the only populated alternative in CRM
(``RDBomHdrs``) is an R&D BOM covering 13% of the affected items. The production
workbook covers 95%.

WHAT IS DELIBERATELY EXCLUDED
-----------------------------
Price rows with a previous price of 1.00 or less (placeholders) and moves beyond
+/-100% are filtered at sync — 12 of 365 increases exceeded +100%, one claiming
Rs 162.86 -> Rs 2,565.00 for rice bran oil, which alone produced a +425% "impact"
on a product taking 0.15 kg of it. The card reports how many it set aside so the
exclusion is visible rather than silent.
"""
from __future__ import annotations

from datetime import date

from ..integration import planning_filter as _pf
from ..integration import staging
from .dashboard import _pick_persona, _scope_flt, _scope_summary

_PAYLOAD_V = 1
# only these personas may see purchase prices (Admin backs the View-as switcher)
ALLOWED = ("Division Head", "Business Head", "Admin")
# the card's table now carries three tiny columns, so it can hold every impacted
# product rather than a page of them; the cap is only a runaway guard
_TOP = 500
# cost-impact bands for the summary, per the brief's section 4
BANDS = [(">5%", 5.0, None), ("3-5%", 3.0, 5.0), ("1-3%", 1.0, 3.0), ("<1%", None, 1.0)]
# Almost every manufacturing BOM states its components per ONE unit of output —
# the non-packing quantities sum to about 1 (777 of 851 fall between 0.5 and 1.5).
# A handful are written per production batch or per drum instead: one line of
# PUREPRINT WHITE NC PLUS lists 179.25 KG of inputs. Dispatch quantity and item
# cost are both per unit, so a batch BOM has to be divided by its own basis
# before it can be compared with either — left alone that single item reported
# Rs 3,752 of added cost against a Rs 248 unit cost, and Rs 10.1M of exposure,
# six times the rest of the book put together.
_UNIT_BASIS = 1.5       # above this the BOM is not written per unit of output
_MAX_BASIS = 10_000.0   # beyond this the quantity is corrupt (one row reads 3e16)


def _key(name) -> str:
    return _pf._squash(name)


# What a finished good looks like OUTSIDE this module. Purchase prices, supplier
# names, BOM quantities and the per-unit rupee increase are all used to derive
# the figures below and then dropped: the card shows the product, its segment and
# the cost impact, so that is all that leaves the server.
_OUT = ("key", "item", "item_code", "segment2", "segment3", "impact_pct")


def _slim(r: dict) -> dict:
    return {k: r[k] for k in _OUT}


# ── inputs, each cached against what feeds it ────────────────────────────────

_BOM: dict = {}
_SEG: dict = {}


# The activity classes the whole dashboard reports on — see api.item_activity.
# Manufacturing wins when an item carries both, because the recipe is what the
# price rise actually flows through.
from .item_activity import ACTIVITY as _ACTIVITY


def _bom() -> dict:
    """{squashed assembly name: components} for the Performance Chemicals finished
    goods we make or repack — see ``_ACTIVITY`` and ``item_activity.DIVISION``.
    Other divisions, internal builds and traded goods are dropped."""
    from .live import _resolve_file
    path = _resolve_file("PLANNING_BOM_XLSX")
    if _BOM.get("path") == path and _BOM.get("map"):
        return _BOM["map"]
    try:
        raw = _pf.load_bom_detailed(path)
    except Exception:   # noqa: BLE001
        return {}
    out = {}
    from .item_activity import division_map, DIVISION
    _by_code, by_name = division_map()
    for k, variants in raw["by_squash"].items():
        # Performance Chemicals only — the division sits above the activity rule
        div = by_name.get(k)
        if div is None:
            div = next((_by_code.get(_pf._norm(v.get("assembly_item")))
                        for v in variants
                        if _by_code.get(_pf._norm(v.get("assembly_item")))), None)
        if div != DIVISION:
            continue
        for cls in _ACTIVITY:
            cand = [v for v in variants if v.get("bom_class") == cls]
            if cand:
                # the recipe, not the packing line, when the item has both
                out[k] = next((v for v in cand if not v["is_packing"]), cand[0])
                break
    _BOM.update({"path": path, "map": out})
    return out


def _segments() -> dict:
    """{squashed item name: {segment2, segment3, segment4}}."""
    stamp = str((staging.last_sync("item_segments") or {}).get("finished_at") or "")
    if _SEG.get("stamp") == stamp and _SEG.get("map"):
        return _SEG["map"]
    out = {}
    for r in staging.read_item_segments():
        k = _key(r.get("ItemName"))
        if k and k not in out:
            out[k] = {"segment2": r.get("Segment2"), "segment3": r.get("Segment3"),
                      "segment4": r.get("Segment4"), "item_code": r.get("ItemCode")}
    _SEG.update({"stamp": stamp, "map": out})
    return out


def _in_scope(seg: dict, flt: dict) -> bool:
    """A segment-scoped persona sees the items inside its own grants; an Admin
    filter ({}) sees everything."""
    grants = (flt or {}).get("segment_grants")
    if not grants:
        return True
    for g in grants:
        if seg.get(g["level"]) and seg[g["level"]] == g["value"]:
            return True
    return False


def _fg_money(n_jc: int = 3) -> tuple[dict, dict]:
    """({item key: unit cost}, {item key: (qty per cycle, value per cycle)}) —
    volume is the recent run rate, not the whole 13-cycle window, so 'exposure
    per cycle' means something."""
    cost = {}
    for r in staging.read_stock_details():
        c = float(r.get("ItemCost") or 0)
        k = _key(r.get("ItemDesc"))
        if c > 0 and k and k not in cost:
            cost[k] = c
    from ..integration import mysql_db
    sales: dict = {}
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(jc_index) m FROM stg_dispatch_scope")
                top = int((cur.fetchone() or {}).get("m") or 0)
                ew, ep = staging._excl_where()
                cur.execute("SELECT item_name, SUM(qty) q, SUM(value_) v "
                            "FROM stg_dispatch_scope WHERE jc_index >= %s "
                            + "".join(" AND " + c for c in ew) +
                            " GROUP BY item_name",
                            tuple([max(0, top - n_jc + 1)] + ep))
                for r in cur.fetchall():
                    k = _key(r["item_name"])
                    if not k:
                        continue
                    q, v = sales.get(k, (0.0, 0.0))
                    sales[k] = (q + float(r["q"] or 0), v + float(r["v"] or 0))
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        pass
    return cost, {k: (q / n_jc, v / n_jc) for k, (q, v) in sales.items()}


# ── the card ─────────────────────────────────────────────────────────────────

_CACHE: dict = {}


def _band(pct) -> str:
    if pct is None:
        return "unknown"
    for label, lo, hi in BANDS:
        if (lo is None or pct > lo) and (hi is None or pct <= hi):
            return label
    return "<1%"


def build(persona: str, flt: dict, top: int | None = _TOP) -> dict:
    """``top=None`` returns every impacted FG — the Excel download, which must
    not be trimmed to what the card shows. Either way the rows are slimmed to
    ``_OUT``; the raw-material detail never leaves this function."""
    moves = {r["item_code"]: r for r in staging.read_rm_price_moves()}
    counts = staging.rm_price_counts()
    bom, segs = _bom(), _segments()
    cost, sales = _fg_money()

    fgs, used_rms = [], set()
    for k, v in bom.items():
        seg = segs.get(k) or {}
        if not _in_scope(seg, flt):
            continue
        basis = sum(float(c.get("qty") or 0) for c in v["components"]
                    if not _pf._is_packing_comp(c))
        if basis > _MAX_BASIS:
            continue
        added, behind = 0.0, []
        for c in v["components"]:
            m = moves.get(str(c.get("comp_code") or ""))
            qty = float(c.get("qty") or 0)
            if not m or qty <= 0:
                continue
            delta = qty * (m["new_price"] - m["old_price"])
            added += delta
            used_rms.add(m["item_code"])
            behind.append({"rm": m["item_name"], "rm_code": m["item_code"],
                           "bom_qty": round(qty, 4),
                           "old_price": m["old_price"], "new_price": m["new_price"],
                           "pct": m["pct"], "added": round(delta, 2),
                           "moved_on": m["moved_on"]})
        if basis > _UNIT_BASIS:
            added /= basis
            for b in behind:
                b["added"] = round(b["added"] / basis, 4)
        if added <= 0:
            continue
        unit_cost = cost.get(k)
        qty_cyc, val_cyc = sales.get(k, (0.0, 0.0))
        sell = (val_cyc / qty_cyc) if qty_cyc > 0 else None
        behind.sort(key=lambda x: -x["added"])
        fgs.append({
            "key": k, "item": v["assembly_desc"], "item_code": seg.get("item_code"),
            "segment2": seg.get("segment2"), "segment3": seg.get("segment3"),
            "added_per_unit": round(added, 2),
            "unit_cost": round(unit_cost, 2) if unit_cost else None,
            "impact_pct": round(100.0 * added / unit_cost, 1) if unit_cost else None,
            "qty_per_cycle": round(qty_cyc, 1),
            "revenue_per_cycle": round(val_cyc, 0),
            "sell_price": round(sell, 2) if sell else None,
            "exposure": round(added * qty_cyc, 0),
            "margin_erosion_pts": round(100.0 * added / sell, 1) if sell and sell > 0 else None,
            "rm_count": len(behind),
        })
    fgs.sort(key=lambda r: (-(r["impact_pct"] or 0), -r["exposure"]))

    exposure = sum(r["exposure"] for r in fgs)
    revenue = sum(r["revenue_per_cycle"] for r in fgs)
    weighted = [(r["impact_pct"], r["qty_per_cycle"]) for r in fgs
                if r["impact_pct"] is not None and r["qty_per_cycle"] > 0]
    wq = sum(q for _p, q in weighted)
    return {
        "v": _PAYLOAD_V, "allowed": True, "persona": persona,
        "as_of": date.today().isoformat(),
        "window_days": 90,
        "kpis": {
            "rms_up": len(used_rms),
            "rms_up_all": counts.get("rises", 0),
            "flagged_out": counts.get("flagged", 0),
            "fgs_impacted": len(fgs),
            "avg_impact_pct": (round(sum(p * q for p, q in weighted) / wq, 1) if wq else None),
            "exposure_per_cycle": round(exposure, 0),
            "revenue_per_cycle": round(revenue, 0),
            "margin_erosion_pts": (round(100.0 * exposure / revenue, 2) if revenue else None),
            "with_cost": sum(1 for r in fgs if r["unit_cost"]),
            "with_sales": sum(1 for r in fgs if r["qty_per_cycle"] > 0),
        },
        "bands": [{"band": lbl,
                   "fgs": sum(1 for r in fgs if _band(r["impact_pct"]) == lbl),
                   "exposure": round(sum(r["exposure"] for r in fgs
                                         if _band(r["impact_pct"]) == lbl), 0)}
                  for lbl, _lo, _hi in BANDS],
        "fgs": [_slim(r) for r in (fgs if top is None else fgs[:top])],
        "total_fgs": len(fgs),
        "last_sync": staging.last_sync("rm_price_moves"),
    }


def rm_impact(username: str | None = None, email: str | None = None,
              admin: bool = False, persona: str | None = None,
              full: bool = False) -> dict:
    """Gated: only Division Head, Business Head and Admin get the data.

    ``full`` skips the card's 60-row cap — the Excel download goes through the
    same gate, so a blocked persona cannot reach prices that way either."""
    if admin:
        who, flt = "Admin", {}
    else:
        grants = staging.read_user_scope(email=email or None, username=username or None) \
            if (email or username) else []
        if not (persona and any(g["persona"] == persona for g in grants)):
            persona = _pick_persona(grants)
        if persona not in ALLOWED:
            return {"v": _PAYLOAD_V, "allowed": False, "persona": persona,
                    "reason": "Purchase prices are limited to Division Head "
                              "and Business Head."}
        _st, _mine, flt = _scope_flt(persona, grants)
        who = persona
        if flt is None:
            return {"v": _PAYLOAD_V, "allowed": False, "persona": persona,
                    "reason": "No data scope is mapped to this account."}

    stamp = "|".join([
        str((staging.last_sync("rm_price_moves") or {}).get("finished_at") or ""),
        str((staging.last_sync("stock_details") or {}).get("finished_at") or ""),
        str((staging.last_sync("dispatch_scope") or {}).get("finished_at") or ""),
        f"v{_PAYLOAD_V}"])
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    ck = (username or "", email or "", bool(admin), who, bool(full))
    if ck not in _CACHE:
        p = build(who, flt, None if full else _TOP)
        if not admin:
            _st, mine, _f = _scope_flt(who, staging.read_user_scope(
                email=email or None, username=username or None))
            p["scope"] = _scope_summary(who, _st, mine)
        else:
            p["scope"] = ["Full access — all divisions"]
        _CACHE[ck] = p
    return _CACHE[ck]


__all__ = ["rm_impact", "build", "ALLOWED"]
