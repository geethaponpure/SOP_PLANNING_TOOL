"""Promise Dates — Phase 3 of the Projection -> SOC -> Supply work.

Phase 2 answered "is there enough". This answers "**when**": if I raise the order
now, what date can supply chain actually promise, and when does the stock run out
if I don't?

THE SUPPLY TIMELINE
-------------------
Per item, every dated supply event is put on one ladder and the company's dated
firm orders are burnt down against it, oldest first:

    on hand today          stock at the orgs that ship, available now
    production             a job on the newest saved JC plan: its end date plus
                           the standard receipt lead
    inbound purchase       an open PO — CRM carries NO expected-arrival date, so
                           the date is ESTIMATED as po_date + the item's average
                           lead time from our own receipt history, and every such
                           event is flagged ``estimate``

From the walk come the three numbers the proposal asks for:

    ctp_date        earliest date the running balance covers the quantity needed
                    (section 4 — "commitment date from the supply timeline")
    risk_date       first date the running balance goes negative, i.e. the day
                    committed orders exhaust the stock
    days_to_risk    risk_date - today  (section 13)
    slip_days       ctp_date - required_date  (section 5's "Commitment Risk: 7 Days")

WHAT THE REQUIRED DATE CAN BE
-----------------------------
A projection carries no day-level date — only which half of the cycle the planner
put the quantity in. So the requirement is dated to a fortnight: quantity entered
in week 1 is required by the cycle's midpoint, week 2 by the cycle end. Precise
day-level slippage is only available where a CUSTOMER requested date exists, and
that lives on order lines, not projections.

SAFETY STOCK IS A WARNING, NOT A WALL
-------------------------------------
Phase 2's ATP subtracts MSL because it answers "how much is safe to plan
against". A promise date answers "when can we physically deliver", so MSL is NOT
subtracted here — it would refuse promises on the 45% of exposed items already
sitting below their safety level. Instead a promise that would dip under MSL is
flagged ``breaches_msl``.

THE HONEST CEILING
------------------
Only 83 of 347 exposed items carry any forward supply at all (34 production, 49
inbound). For the rest there is no dated supply to promise from — not because the
company cannot supply them, but because nothing planned is visible to us. Those
are reported as "no dated supply" rather than given an invented date.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..integration import planning_filter as _pf
from ..integration import planning_settings as _ps
from ..integration import staging
from . import competition as _comp
from .dashboard import _scope_summary

_PAYLOAD_V = 1
_ROW_CAP = 400
# how far forward the walk looks before giving up on a promise date
HORIZON_DAYS = 240
# a JC is four weeks; the projection's "week 1" half is required by its midpoint
HALF_CYCLE_DAYS = 13
# fallback when an item has no receipt history to derive a lead time from
DEFAULT_PO_LEAD_DAYS = 14

CLASSES = [
    ("none", "No dated supply"),
    ("late", "Promise slips past the requirement"),
    ("dated", "Can be promised in time"),
    ("ready", "Available now"),
    ("covered", "Already firm"),
]
_CLASS_LABEL = dict(CLASSES)


def _key(name) -> str:
    return _pf._squash(name)


# ── inbound purchase, with an ESTIMATED arrival date ─────────────────────────

_PO: dict = {}


def _po_arrivals() -> dict:
    """{item key: [{date, qty, estimate}]} for open purchase orders.

    BiPoDetails has no expected-arrival column — only the PO date, the ordered
    quantity and what has been received. The arrival is therefore MODELLED as
    po_date + the item's average lead time measured from our own receipt history
    (median across items is 14 days). Every event carries estimate=True so the
    page can label it; never present these as promised dates."""
    stamp = str((staging.last_sync("intransit") or {}).get("finished_at") or "")
    if _PO.get("stamp") == stamp:
        return _PO["map"]
    try:
        from .live import _po_intel
        intel = _po_intel() or {}
    except Exception:   # noqa: BLE001
        intel = {}
    out: dict = {}
    for r in staging.read_intransit() or []:
        qty = float(r.get("InTransit") or 0)
        if qty <= 0:
            continue
        k = _key(r.get("Item_Desc"))
        if not k:
            continue
        pod = str(r.get("Po_Date") or "")[:10]
        if not pod:
            continue
        lead = (intel.get(str(r.get("Item_Code") or "")) or {}).get("avg_lead_time_days")
        lead = int(lead) if lead else DEFAULT_PO_LEAD_DAYS
        try:
            eta = (date.fromisoformat(pod) + timedelta(days=lead)).isoformat()
        except ValueError:
            continue
        out.setdefault(k, []).append({"date": eta, "qty": round(qty, 1),
                                      "source": "inbound", "estimate": True,
                                      "note": f"PO {r.get('Po_Number') or ''} "
                                              f"placed {pod} + {lead}d lead"})
    _PO.update({"stamp": stamp, "map": out})
    return out


def _production_events(sup: dict) -> dict:
    """{item key: [{date, qty}]} from the saved plan, plus the receipt lead."""
    std = int(_ps.load().get("receipt_std_lead_days", 3))
    out: dict = {}
    for k, v in (sup.get("incoming") or {}).items():
        if not v.get("date") or v.get("qty", 0) <= 0:
            continue
        try:
            avail = (date.fromisoformat(v["date"]) + timedelta(days=std)).isoformat()
        except ValueError:
            continue
        out.setdefault(k, []).append({"date": avail, "qty": round(v["qty"], 1),
                                      "source": "production", "estimate": False,
                                      "note": f"plan job ends {v['date']} + {std}d receipt"})
    return out


# ── the walk ─────────────────────────────────────────────────────────────────

def _walk(on_hand: float, supply: list[dict], demand: list[dict],
          today: date, need: float, msl: float) -> dict:
    """Burn the company's dated firm orders down against dated supply.

    Anything already due is applied at ``today`` — it is owed now, so it consumes
    stock before anything promised later can."""
    horizon = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    t0 = today.isoformat()
    events: dict = {}
    for e in supply:
        d = max(e["date"], t0)
        if d > horizon:
            continue
        events.setdefault(d, {"in": 0.0, "out": 0.0})["in"] += e["qty"]
    for e in demand:
        d = max(e["date"] or t0, t0)      # undated or overdue -> owed today
        if d > horizon:
            continue
        events.setdefault(d, {"in": 0.0, "out": 0.0})["out"] += e["qty"]

    # Always evaluate a node at TODAY. Seeding the promise from the opening
    # balance would ignore orders that are already past due — those are applied
    # today, and must consume the stock before anything can be promised out of it.
    events.setdefault(t0, {"in": 0.0, "out": 0.0})

    bal = on_hand
    risk_date = None
    ctp = t0 if need <= 0 else None
    low = bal
    ladder = []
    for d in sorted(events):
        ev = events[d]
        bal += ev["in"] - ev["out"]
        low = min(low, bal)
        if bal < 0 and risk_date is None:
            risk_date = d
        if ctp is None and bal >= need:
            ctp = d
        ladder.append({"date": d, "in": round(ev["in"], 1), "out": round(ev["out"], 1),
                       "balance": round(bal, 1)})
    return {"ctp": ctp, "risk_date": risk_date, "closing": round(bal, 1),
            "low": round(low, 1), "ladder": ladder[:60],
            "breaches_msl": bool(msl > 0 and low < msl)}


def _classify(need: float, ctp, required: str) -> str:
    if need <= 0:
        return "covered"
    if ctp is None:
        return "none"
    if ctp <= date.today().isoformat():
        return "ready"
    return "dated" if required and ctp <= required else ("late" if required else "dated")


def _days(a, b) -> int | None:
    if not a or not b:
        return None
    try:
        return (date.fromisoformat(a) - date.fromisoformat(b)).days
    except ValueError:
        return None


# ── payload ──────────────────────────────────────────────────────────────────

_CACHE: dict = {}


def _required_dates(jc_from: str, jc_to: str) -> tuple[str, str]:
    """(week-1 requirement, week-2 requirement) for the cycle."""
    try:
        start = date.fromisoformat(jc_from)
        return (start + timedelta(days=HALF_CYCLE_DAYS)).isoformat(), jc_to
    except (ValueError, TypeError):
        return jc_to, jc_to


def build_rows(rows: list[dict], jc_from: str, jc_to: str, today: date | None = None):
    """Attach a promise date, a risk date and a slip to every ledger row."""
    today = today or date.today()
    sup = _comp.supply_inputs()
    prod = _production_events(sup)
    po = _po_arrivals()
    sched: dict = {}
    for r in staging.commit_schedule(sup["cutoff"]):
        sched.setdefault(_key(r["item_key"]), []).append(
            {"date": r["due"], "qty": r["qty"]})
    w1_req, w2_req = _required_dates(jc_from, jc_to)

    out = []
    for r in rows:
        k = r["key"]
        supply = (prod.get(k) or []) + (po.get(k) or [])
        need = r["my_unprotected"]
        # the requirement is dated to the half of the cycle the planner used
        required = w2_req if r.get("my_week2", 0) > 0 else w1_req
        w = _walk(r["on_hand"], supply, sched.get(k) or [], today, need, r["msl"])
        cls = _classify(need, w["ctp"], required)
        out.append({
            **{f: r[f] for f in ("key", "item", "item_code", "segment3", "risk",
                                 "my_projection", "my_firm", "my_unprotected",
                                 "on_hand", "msl", "firm_total", "firm_others",
                                 "incoming", "exposure", "all_projection")},
            "need": need,
            "required": required if need > 0 else None,
            "ctp": w["ctp"], "risk_date": w["risk_date"],
            "days_to_risk": _days(w["risk_date"], today.isoformat()),
            "slip_days": _days(w["ctp"], required) if (need > 0 and w["ctp"]) else None,
            "closing": w["closing"], "low": w["low"],
            "breaches_msl": w["breaches_msl"],
            "supply_sources": sorted({e["source"] for e in supply}),
            "estimated": any(e["estimate"] for e in supply),
            "class": cls,
        })
    order = {c: i for i, (c, _l) in enumerate(CLASSES)}
    out.sort(key=lambda x: (order.get(x["class"], 9),
                            -(x["slip_days"] or 0), -(x["need"] or 0)))
    return out


def _totals(rows: list[dict]) -> dict:
    slips = [r["slip_days"] for r in rows if r["slip_days"] and r["slip_days"] > 0]
    risky = [r for r in rows if r["days_to_risk"] is not None]
    return {
        "items": len(rows),
        "need": round(sum(r["need"] for r in rows), 1),
        "promised": sum(1 for r in rows if r["class"] in ("ready", "dated")),
        "late": sum(1 for r in rows if r["class"] == "late"),
        "no_date": sum(1 for r in rows if r["class"] == "none"),
        "no_date_qty": round(sum(r["need"] for r in rows if r["class"] == "none"), 1),
        "worst_slip": max(slips) if slips else 0,
        "avg_slip": round(sum(slips) / len(slips), 1) if slips else 0,
        "slipping_qty": round(sum(r["need"] for r in rows if r["class"] == "late"), 1),
        "running_out": sum(1 for r in risky if (r["days_to_risk"] or 0) <= 14),
        "breaching_msl": sum(1 for r in rows if r["breaches_msl"]),
        "estimated_items": sum(1 for r in rows if r["estimated"]),
        "buckets": [{"key": c, "label": lbl,
                     "items": sum(1 for r in rows if r["class"] == c),
                     "qty": round(sum(r["need"] for r in rows if r["class"] == c), 1)}
                    for c, lbl in CLASSES],
    }


def promise_dates(username: str | None = None, email: str | None = None,
                  admin: bool = False, persona: str | None = None,
                  jc: int | None = None) -> dict:
    stamp = "|".join([
        str((staging.last_sync("projection_customer") or {}).get("finished_at") or ""),
        str((staging.last_sync("order_commit") or {}).get("finished_at") or ""),
        str((staging.last_sync("stock_details") or {}).get("finished_at") or ""),
        str((staging.last_sync("intransit") or {}).get("finished_at") or ""),
        str(date.today()), f"v{_PAYLOAD_V}"])
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    ck = (username or "", email or "", bool(admin), persona or "", jc or 0)
    if ck in _CACHE:
        return _CACHE[ck]

    base_payload = _comp.supply_competition(username, email, admin, persona, jc)
    persona_name = base_payload.get("persona")
    out = {
        "v": _PAYLOAD_V, "persona": persona_name, "today": date.today().isoformat(),
        "jc": base_payload.get("jc"), "jc_label": base_payload.get("jc_label"),
        "jc_from": base_payload.get("jc_from"), "jc_to": base_payload.get("jc_to"),
        "jcs": base_payload.get("jcs") or [],
        "scope": base_payload.get("scope") or [],
        "msl_ref": base_payload.get("msl_ref"), "plan_id": base_payload.get("plan_id"),
        "horizon_days": HORIZON_DAYS, "half_cycle_days": HALF_CYCLE_DAYS,
        "last_sync": base_payload.get("last_sync"),
    }
    if not persona_name or base_payload.get("kpis") is None:
        return {**out, "kpis": None, "rows": [], "total_rows": 0}

    _p, _s, _m, _ay, _jj, ledger = _comp.scoped_ledger(username, email, admin, persona, jc)
    rows = build_rows(ledger, out["jc_from"], out["jc_to"])
    payload = {**out, "kpis": _totals(rows), "rows": rows[:_ROW_CAP],
               "total_rows": len(rows)}
    _CACHE[ck] = payload
    return payload


def item_timeline(item_key: str, username=None, email=None, admin=False,
                  persona=None, jc=None) -> dict:
    """The section-4 ladder for one item: every dated supply source, the firm
    orders burning it down, and the resulting promise and risk dates."""
    p = promise_dates(username, email, admin, persona, jc)
    k = _key(item_key)
    row = next((r for r in (p.get("rows") or []) if r["key"] == k), None)
    if row is None:
        _pp, _s, _m, _ay, _jj, ledger = _comp.scoped_ledger(username, email, admin, persona, jc)
        hit = [r for r in ledger if r["key"] == k]
        if not hit:
            return {"found": False, "item": item_key}
        row = build_rows(hit, p["jc_from"], p["jc_to"])[0]

    sup = _comp.supply_inputs()
    supply = (_production_events(sup).get(k) or []) + (_po_arrivals().get(k) or [])
    sched = [{"date": r["due"], "qty": r["qty"], "lines": r["lines_"]}
             for r in staging.commit_schedule(sup["cutoff"]) if _key(r["item_key"]) == k]
    w = _walk(row["on_hand"], supply, sched, date.today(), row["need"], row["msl"])
    return {
        "found": True, "item": row["item"], "key": k, "row": row,
        "jc_label": p.get("jc_label"), "today": p.get("today"),
        "sources": sorted(supply, key=lambda e: e["date"]),
        "demand": sorted(sched, key=lambda e: (e["date"] or "")),
        "ladder": w["ladder"],
    }


def scoped_rows(username=None, email=None, admin=False, persona=None, jc=None):
    """(persona, rows — UNCAPPED) for exports."""
    p = promise_dates(username, email, admin, persona, jc)
    if p.get("kpis") is None:
        return p, []
    _pp, _s, _m, _ay, _jj, ledger = _comp.scoped_ledger(username, email, admin, persona, jc)
    return p, build_rows(ledger, p["jc_from"], p["jc_to"])


__all__ = ["promise_dates", "item_timeline", "scoped_rows", "build_rows",
           "CLASSES", "_CLASS_LABEL", "_walk"]
