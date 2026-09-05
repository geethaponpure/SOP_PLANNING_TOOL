"""Supply Competition — Phase 2 of the Projection -> SOC -> Supply work.

Phase 1 asked "is my projection backed by a firm order". This asks the harder
question behind it: **if I have not raised the order yet, will the stock still
be there?** Another executive in the same collector or market circle may already
have committed the available supply against a confirmed order.

THE SUPPLY POSITION PER ITEM
----------------------------
    on_hand      stock at orgs that actually sell (the 96 that appear on open
                 orders), minus the non-sellable sub-inventories
    firm_total   open committed balance across the WHOLE company — not just the
                 caller's scope, because everyone competes for the same stock
    firm_mine    the slice of that belonging to the caller's own customers
    firm_others  what colleagues have already committed  (= total - mine)
    msl          the safety level from the latest saved MSL snapshot
    incoming     quantity finishing on the latest saved JC plan's production
                 schedule, with its earliest end date

    atp        = on_hand - firm_total - msl      uncommitted stock, company-wide
    atp_for_me = on_hand - firm_others - msl     what is left for MY unconverted
                                                 projection after everyone
                                                 else's firm claims

A NEGATIVE ATP IS NORMAL, NOT AN ALARM. Measured across the live order book,
on-hand covers only part of committed demand (net -15.8M KG) because this is a
make-to-order manufacturer: production, not stock, fills the order book. That is
why ``incoming`` decides between "recoverable" and "high risk" below.

FIRM DEMAND EXCLUDES THE STALE BOOK. 7,381 of 13,804 open lines are overdue by
more than 90 days — never-closed paperwork rather than live claims on stock.
Counting them would show almost everything as oversold. They are reported
separately as ``stale`` and never consume supply here.

WHO IS COMPETING — AND WHAT WE ARE ALLOWED TO SHOW
--------------------------------------------------
The proposal asks to name the competing executive. CRM cannot: EXECUTIVE_NAME is
"No Sales Credit" on 97% of open lines. It also would not be right to name
customers a persona is not entitled to see, so the drill-down names customers
INSIDE the caller's scope and aggregates everything else by collector and market
circle. Admins see every name. Set SHOW_ALL_HOLDERS to widen this if the
business decides differently.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..integration import msl as _msl
from ..integration import planning_filter as _pf
from ..integration import planning_settings as _ps
from ..integration import staging
from .commit import _commit_flt
from .dashboard import _pick_persona, _scope_flt, _scope_summary

_PAYLOAD_V = 3
_ROW_CAP = 400
# Open lines older than this are dead paperwork, not claims on stock (see above).
STALE_DAYS = 90
# When True, every persona sees the competing customers by name. Default False:
# a persona sees names only inside its own scope, others rolled up by collector.
SHOW_ALL_HOLDERS = False

RISK = [
    ("critical", "Critical — no stock, no production"),
    ("high", "High risk — committed away"),
    ("at_risk", "At risk — needs production"),
    ("safe", "Safe — supply available"),
    ("covered", "Already firm"),
]
_RISK_LABEL = dict(RISK)


def _key(name) -> str:
    return _pf._squash(name)


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


# ── supply inputs (cached against the syncs that feed them) ──────────────────

_SUPPLY: dict = {}


def _sell_orgs(commit_rows) -> set:
    """Orgs that actually ship to customers — the ones appearing on open orders.
    The planning ``warehouse_orgs`` list is a different concept (MFG plants that
    feed the RM plan); using it here hides the branch and port stock the orders
    are actually drawn from, and drops item coverage from 89% to 45%."""
    return {str(r.get("inv_org") or "") for r in commit_rows if r.get("inv_org")}


def _on_hand(sell_orgs: set) -> tuple[dict, dict]:
    """({item key: sellable qty}, {item key: {org: qty}})."""
    s = _ps.load()
    excl = {x.lower() for x in s.get("excluded_subinv", _pf.EXCLUDE_SUBINV)}
    total: dict = {}
    by_org: dict = {}
    for r in staging.read_stock_details():
        q = float(r.get("Qty") or 0)
        if q <= 0 or str(r.get("SubInv") or "").lower() in excl:
            continue
        org = str(r.get("Organization") or "")
        if org not in sell_orgs:
            continue
        k = _key(r.get("ItemDesc"))
        if not k:
            continue
        total[k] = total.get(k, 0.0) + q
        by_org.setdefault(k, {})
        by_org[k][org] = by_org[k].get(org, 0.0) + q
    return total, by_org


def _msl_levels() -> tuple[dict, str | None]:
    snaps = _msl.list_snapshots()
    if not snaps:
        return {}, None
    ref = snaps[0].get("reference")
    snap = _msl.get_snapshot(ref) or {}
    out: dict = {}
    for r in snap.get("rows") or []:
        k = _key(r.get("item_name"))
        if k:
            out[k] = out.get(k, 0.0) + float(r.get("msl") or 0)
    return out, ref


_INCOMING: dict = {}
_ALL_PROJ: dict = {}


def _all_projection(acc_year: str, jc: int) -> dict:
    """{item key: {qty, customers, collectors}} projected by EVERYONE for this
    cycle. Cached per cycle against the projection sync."""
    stamp = str((staging.last_sync("projection_customer") or {}).get("finished_at") or "")
    ck = (acc_year, int(jc), stamp)
    if _ALL_PROJ.get("key") == ck:
        return _ALL_PROJ["map"]
    out = {_key(r["item_key"]): r for r in staging.projection_by_item(acc_year, jc)}
    _ALL_PROJ.update({"key": ck, "map": out})
    return out


def _incoming() -> tuple[dict, int | None]:
    """{item key: {qty, date}} from the newest saved JC plan's production
    schedule. Manufacturing items with a BOM only — traded goods have no
    production job, so they simply carry no incoming supply here.

    Cached on the PLAN, not on the sync stamp: building the schedule costs ~18s
    (BOM explosion + vessel scheduling) while the rest of the supply picture
    rebuilds in ~1.4s. The plan only changes when someone saves a new one, so
    tying it to the 20-minute sync would repay that 18s for no new information."""
    try:
        from ..integration import mysql_db as _mysql
        from .live import _production_schedule
        plans = _mysql.list_jc_plans()
        if not plans:
            return {}, None
        pid = plans[0].get("plan_id")
        if _INCOMING.get("pid") == pid:
            return _INCOMING["map"], pid
        out: dict = {}
        for j in (_production_schedule(pid) or {}).get("jobs") or []:
            k = _key(j.get("item"))
            end = str(j.get("end") or "")[:10]
            if not k:
                continue
            a = out.setdefault(k, {"qty": 0.0, "date": None})
            a["qty"] += float(j.get("qty") or 0)
            if end and (a["date"] is None or end < a["date"]):
                a["date"] = end
        _INCOMING.update({"pid": pid, "map": out})
        return out, pid
    except Exception:   # noqa: BLE001
        return {}, None


def supply_inputs() -> dict:
    """The company-wide supply picture, rebuilt only when a feeding sync moves."""
    stamp = "|".join([
        str((staging.last_sync("stock_details") or {}).get("finished_at") or ""),
        str((staging.last_sync("order_commit") or {}).get("finished_at") or ""),
        f"v{_PAYLOAD_V}"])
    if _SUPPLY.get("stamp") == stamp:
        return _SUPPLY["data"]
    cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    firm = staging.commit_by_item(None, cutoff)
    sell = {str(r["inv_org"]) for r in staging.commit_orgs() if r.get("inv_org")}
    on_hand, by_org = _on_hand(sell)
    msl_map, msl_ref = _msl_levels()
    inc, pid = _incoming()
    data = {"firm": {_key(r["item_key"]): r for r in firm},
            "on_hand": on_hand, "by_org": by_org, "msl": msl_map, "msl_ref": msl_ref,
            "incoming": inc, "plan_id": pid, "cutoff": cutoff,
            "sell_orgs": len(sell)}
    _SUPPLY.update({"stamp": stamp, "data": data})
    return data


# ── the per-item ledger ──────────────────────────────────────────────────────

def _classify(r: dict) -> str:
    if r["my_unprotected"] <= 0:
        return "covered"
    if r["exposure"] <= 0:
        return "safe"
    if r["incoming"] > 0:
        return "at_risk"
    if r["on_hand"] > 0:
        return "high"
    return "critical"


def _ledger(flt: dict, flt_c: dict, acc_year: str, jc: int) -> list[dict]:
    """One row per item in the caller's world: what they projected, what they
    have firm, what the company holds, and what colleagues have claimed.

    ``flt`` scopes stg_projection_customer (collectors by id); ``flt_c`` scopes
    stg_order_commit, which carries collector NAMES only — using the projection
    filter there would silently leave a collector-scoped persona unrestricted and
    show them the whole company's order book as their own."""
    if flt is None or flt_c is None:
        return []
    sup = supply_inputs()
    cutoff = sup["cutoff"]

    proj: dict = {}
    for r in staging.read_projection_customer(flt, acc_year, jc):
        k = _key(r.get("item_name"))
        if not k:
            continue
        a = proj.setdefault(k, {"item": r.get("item_name"), "item_code": r.get("item_code"),
                                "segment3": r.get("segment3"), "qty": 0.0,
                                "week1": 0.0, "week2": 0.0, "customers": set()})
        a["qty"] += r["current_q"]
        a["week1"] += r["week1_q"]
        a["week2"] += r["week2_q"]
        a["customers"].add(r.get("customer_id"))

    everyone = _all_projection(acc_year, jc)
    mine: dict = {}
    for r in staging.commit_by_item(flt_c, cutoff):
        k = _key(r["item_key"])
        if k:
            mine[k] = r

    out = []
    for k in set(proj) | set(mine):
        p = proj.get(k) or {}
        m = mine.get(k) or {}
        f = sup["firm"].get(k) or {}
        inc = sup["incoming"].get(k) or {}
        on_hand = round(sup["on_hand"].get(k, 0.0), 1)
        msl = round(sup["msl"].get(k, 0.0), 1)
        firm_total = round(float(f.get("balance") or 0), 1)
        firm_mine = round(float(m.get("balance") or 0), 1)
        # a persona's own book can only ever be part of the company total; guard
        # against a name that matched differently on the two sides
        firm_mine = min(firm_mine, firm_total)
        firm_others = round(firm_total - firm_mine, 1)
        my_proj = round(float(p.get("qty") or 0), 1)
        my_unprot = round(max(0.0, my_proj - firm_mine), 1)
        ev = everyone.get(k) or {}
        all_proj = round(float(ev.get("qty") or 0), 1)
        atp = round(on_hand - firm_total - msl, 1)
        atp_me = round(on_hand - firm_others - msl, 1)
        row = {
            "key": k,
            "item": p.get("item") or m.get("item_name") or f.get("item_name") or k,
            "item_code": p.get("item_code") or m.get("item_code") or f.get("item_code"),
            "segment3": p.get("segment3"),
            "my_projection": my_proj,
            "my_firm": firm_mine,
            "my_unprotected": my_unprot,
            "my_customers": len(p.get("customers") or ()),
            # the projection's only requirement-date signal: which half of the
            # cycle the planner put the quantity in (there is no day-level date)
            "my_week1": round(float(p.get("week1") or 0), 1),
            "my_week2": round(float(p.get("week2") or 0), 1),
            # section 9: the same item as the whole company sees it
            "all_projection": all_proj,
            "all_customers": int(ev.get("customers") or 0),
            "all_collectors": int(ev.get("collectors") or 0),
            # unfirm demand nobody has converted yet, company-wide
            "all_unprotected": round(max(0.0, all_proj - firm_total), 1),
            "on_hand": on_hand, "msl": msl,
            "firm_total": firm_total, "firm_others": firm_others,
            "other_customers": int(f.get("customers") or 0) - int(m.get("customers") or 0),
            "other_lines": int(f.get("lines_") or 0) - int(m.get("lines_") or 0),
            "stale": round(float(f.get("stale") or 0), 1),
            "incoming": round(float(inc.get("qty") or 0), 1),
            "incoming_date": inc.get("date"),
            "atp": atp, "atp_for_me": atp_me,
        }
        row["exposure"] = round(max(0.0, row["my_unprotected"] - max(0.0, atp_me)), 1)
        row["risk"] = _classify(row)
        row["other_customers"] = max(0, row["other_customers"])
        row["other_lines"] = max(0, row["other_lines"])
        out.append(row)
    out.sort(key=lambda r: (-r["exposure"], -r["my_unprotected"]))
    return out


def _totals(rows: list[dict]) -> dict:
    by_risk = {}
    for r in rows:
        a = by_risk.setdefault(r["risk"], {"items": 0, "qty": 0.0})
        a["items"] += 1
        a["qty"] += r["exposure"] if r["risk"] not in ("covered", "safe") else r["my_unprotected"]
    exposed = [r for r in rows if r["exposure"] > 0]
    return {
        "items": len(rows),
        "my_projection": round(sum(r["my_projection"] for r in rows), 1),
        "my_firm": round(sum(r["my_firm"] for r in rows), 1),
        "my_unprotected": round(sum(r["my_unprotected"] for r in rows), 1),
        "all_projection": round(sum(r["all_projection"] for r in rows), 1),
        "all_unprotected": round(sum(r["all_unprotected"] for r in rows), 1),
        "exposure": round(sum(r["exposure"] for r in rows), 1),
        "exposed_items": len(exposed),
        "on_hand": round(sum(r["on_hand"] for r in rows), 1),
        "firm_total": round(sum(r["firm_total"] for r in rows), 1),
        "firm_others": round(sum(r["firm_others"] for r in rows), 1),
        "incoming": round(sum(r["incoming"] for r in rows), 1),
        "stale": round(sum(r["stale"] for r in rows), 1),
        "buckets": [{"key": k, "label": _RISK_LABEL[k],
                     "items": (by_risk.get(k) or {}).get("items", 0),
                     "qty": round((by_risk.get(k) or {}).get("qty", 0.0), 1)}
                    for k, _lbl in RISK],
    }


def _holders_rollup(rows: list[dict], keyfn, label: str) -> list[dict]:
    agg: dict = {}
    for r in rows:
        k = keyfn(r)
        if not k:
            continue
        a = agg.setdefault(k, {label: k, "balance": 0.0, "lines": 0,
                               "customers": set(), "items": set()})
        a["balance"] += r["balance"]
        a["lines"] += 1
        a["customers"].add(r.get("customer_id"))
        a["items"].add(r.get("item_key"))
    out = []
    for a in agg.values():
        a["customers"] = len(a["customers"])
        a["items"] = len(a["items"])
        a["balance"] = round(a["balance"], 1)
        out.append(a)
    out.sort(key=lambda x: -x["balance"])
    return out


# ── payload ──────────────────────────────────────────────────────────────────

_CACHE: dict = {}


def _context(jc=None) -> tuple[str, int]:
    from ..integration import jc_calendar as _jc
    entry = _jc.planning_jc_entry() or _jc.current_jc_entry() or {}
    return (entry.get("fy") or ""), (int(jc) if jc else int(entry.get("jc") or 0))


def resolve_scope(username=None, email=None, admin=False, persona=None):
    """(persona, stype, grants, projection filter, order-book filter)."""
    if admin:
        return "Admin", "", [], {}, {}
    grants = staging.read_user_scope(email=email or None, username=username or None) \
        if (email or username) else []
    if not (persona and any(g["persona"] == persona for g in grants)):
        persona = _pick_persona(grants)
    if not persona:
        return None, "", [], None, None
    stype, mine, flt = _scope_flt(persona, grants)
    _st, _mn, flt_c = _commit_flt(persona, grants)
    return persona, stype, mine, flt, flt_c


def scoped_ledger(username=None, email=None, admin=False, persona=None, jc=None):
    """(persona, stype, mine, acc_year, jc, rows — UNCAPPED) for exports."""
    persona, stype, mine, flt, flt_c = resolve_scope(username, email, admin, persona)
    acc_year, want = _context(jc)
    rows = _ledger(flt, flt_c, acc_year, want) if flt is not None else []
    return persona, stype, mine, acc_year, want, rows


def supply_competition(username: str | None = None, email: str | None = None,
                       admin: bool = False, persona: str | None = None,
                       jc: int | None = None) -> dict:
    acc_year, want = _context(jc)
    stamp = "|".join([
        str((staging.last_sync("projection_customer") or {}).get("finished_at") or ""),
        str((staging.last_sync("order_commit") or {}).get("finished_at") or ""),
        str((staging.last_sync("stock_details") or {}).get("finished_at") or ""),
        f"v{_PAYLOAD_V}"])
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    ck = (username or "", email or "", bool(admin), persona or "", want)
    if ck in _CACHE:
        return _CACHE[ck]

    persona, stype, mine, flt, flt_c = resolve_scope(username, email, admin, persona)
    from ..integration import jc_calendar as _jc
    entry = next((j for j in _jc.all_jcs()
                  if j["fy"] == acc_year and int(j["jc"]) == want), {}) or {}
    sup = supply_inputs()
    base = {
        "v": _PAYLOAD_V, "persona": persona, "acc_year": acc_year, "jc": want,
        "jc_label": entry.get("label") or f"JC{want}",
        "jc_from": entry.get("from"), "jc_to": entry.get("to"),
        "jcs": [{"jc": j, "label": f"JC{j}"}
                for j in staging.projection_customer_jcs(acc_year)],
        "msl_ref": sup["msl_ref"], "plan_id": sup["plan_id"],
        "sell_orgs": sup["sell_orgs"], "stale_days": STALE_DAYS,
        "last_sync": staging.last_sync("order_commit"),
    }
    if persona is None or flt is None:
        return {**base, "scope": [], "kpis": None, "rows": [], "total_rows": 0,
                "by_collector": [], "by_mc": []}

    rows = _ledger(flt, flt_c, acc_year, want)

    # who holds the competing firm demand, for the items the caller is exposed on
    exposed = [r["key"] for r in rows if r["exposure"] > 0][:_ROW_CAP]
    holders = staging.commit_holders(exposed, sup["cutoff"]) if exposed else []
    my_customers = {r["customer_id"] for r in staging.read_order_commit(flt_c)
                    if r.get("customer_id") is not None}
    for h in holders:
        h["balance"] = float(h.get("balance") or 0)
        h["item_key"] = _key(h.get("item_key"))
        h["mine"] = h.get("customer_id") in my_customers
    others = [h for h in holders if not h["mine"]]

    payload = {
        **base,
        "scope": _scope_summary(persona, stype, mine),
        "kpis": _totals(rows),
        "rows": rows[:_ROW_CAP],
        "total_rows": len(rows),
        "by_collector": _holders_rollup(others, lambda h: h.get("collector"), "collector")[:60],
        "by_mc": _holders_rollup(others, lambda h: h.get("mc_code"), "mc_code")[:60],
        "show_names": bool(admin or SHOW_ALL_HOLDERS),
    }
    _CACHE[ck] = payload
    return payload


def item_competition(item_key: str, username=None, email=None, admin=False,
                     persona=None, jc=None) -> dict:
    """The §7 drill-down for one item: my requirement, the supply position, and
    the other commitments consuming it."""
    persona, stype, mine, acc_year, want, rows = scoped_ledger(
        username, email, admin, persona, jc)
    k = _key(item_key)
    row = next((r for r in rows if r["key"] == k), None)
    if row is None:
        return {"item": item_key, "found": False}
    sup = supply_inputs()
    _p, _s, _m, _flt, flt_c = resolve_scope(username, email, admin, persona)
    my_customers = {r["customer_id"] for r in staging.read_order_commit(flt_c or {})
                    if r.get("customer_id") is not None}
    holders = staging.commit_holders([k], sup["cutoff"])
    show_names = bool(admin or SHOW_ALL_HOLDERS)
    named, hidden = [], {}
    for h in holders:
        h["balance"] = round(float(h.get("balance") or 0), 1)
        h["mine"] = h.get("customer_id") in my_customers
        if show_names or h["mine"]:
            named.append(h)
        else:
            g = hidden.setdefault((h.get("collector"), h.get("mc_code")),
                                  {"collector": h.get("collector"), "mc_code": h.get("mc_code"),
                                   "balance": 0.0, "customers": set(), "lines": 0})
            g["balance"] += h["balance"]
            g["customers"].add(h.get("customer_id"))
            g["lines"] += 1
    named.sort(key=lambda h: -h["balance"])
    grouped = sorted(({"collector": g["collector"], "mc_code": g["mc_code"],
                       "balance": round(g["balance"], 1), "customers": len(g["customers"]),
                       "lines": g["lines"]} for g in hidden.values()),
                     key=lambda g: -g["balance"])
    return {
        "found": True, "item": row["item"], "key": k, "row": row,
        "jc_label": f"JC{want}", "show_names": show_names,
        "holders": named[:200], "grouped": grouped[:60],
        "by_org": sorted(({"org": o, "qty": round(q, 1)}
                          for o, q in (sup["by_org"].get(k) or {}).items()),
                         key=lambda x: -x["qty"])[:40],
    }


__all__ = ["supply_competition", "item_competition", "resolve_scope", "scoped_ledger",
           "RISK", "_RISK_LABEL", "_ledger", "_totals", "supply_inputs"]
