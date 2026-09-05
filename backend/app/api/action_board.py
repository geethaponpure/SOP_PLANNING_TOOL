"""My Supply Position — one board instead of three pages.

The Demand-Protection, Supply-Competition and Promise-Dates pages each answer one
question well, but a sales executive wants them in a single view: what did I
promise, what is firm, what is exposed, and what do I do about it. This module
assembles exactly the three blocks in the requested layout:

  1. a headline strip     projection / firm / protected / at risk / critical,
                          where protected + at_risk + critical == projection
  2. an action list       one row per CUSTOMER x ITEM needing attention, with the
                          item's supply position and a promise date beside it
  3. a why-at-risk panel  the full derivation for one row, ending in the action

GRAIN — AND THE ONE THING TO KEEP IN MIND
-----------------------------------------
Projection and firm orders are per customer x item; supply is per ITEM. A row's
"other SOC" and "available after firm commitments" are therefore the item's
figures, shared across every customer row for that item — the same way the
requested layout shows them. Two rows for the same item are competing for that
one pool, so their exposures cannot simply be added; the headline strip counts
each item's available supply once.

Everything here re-uses the engines the other pages are built on, so the numbers
agree: demand._ledger (customer x item), competition._ledger (item supply and
competing firm demand) and promise._walk (the dated supply ladder).
"""
from __future__ import annotations

from datetime import date

from ..integration import staging
from . import competition as _comp
from . import demand as _dem
from . import promise as _prom

_PAYLOAD_V = 1
# rows returned to the page; the export is uncapped
_ROW_CAP = 300

RISK = [
    ("critical", "Critical"),
    ("watch", "At risk"),
    ("safe", "Safe"),
]
_RISK_LABEL = dict(RISK)


def _cls(unprotected: float, shortfall: float, ctp, required) -> str:
    """Green ONLY when the projection is already ordered.

    Any unconverted quantity is by definition unprotected — nothing is holding
    that stock for this customer until the order exists — so it is never green.
    It goes red when there is a shortage AND no date that meets the requirement;
    amber when supply can still close the gap in time.

    This keeps the headline honest: protected + at risk + critical add back to
    the projection, and every non-green row appears in the action list."""
    if unprotected <= 0:
        return "safe"
    late = (ctp is None) or bool(required and ctp > required)
    return "critical" if (shortfall > 0 and late) else "watch"


def build(username=None, email=None, admin=False, persona=None, jc=None) -> dict:
    persona_name, stype, mine, flt, flt_c = _comp.resolve_scope(username, email, admin, persona)
    acc_year, want = _comp._context(jc)
    from ..integration import jc_calendar as _jc
    entry = next((j for j in _jc.all_jcs()
                  if j["fy"] == acc_year and int(j["jc"]) == want), {}) or {}
    today = date.today()
    base = {
        "v": _PAYLOAD_V, "persona": persona_name, "today": today.isoformat(),
        "acc_year": acc_year, "jc": want,
        "jc_label": entry.get("label") or f"JC{want}",
        "jc_from": entry.get("from"), "jc_to": entry.get("to"),
        "jcs": [{"jc": j, "label": f"JC{j}"}
                for j in staging.projection_customer_jcs(acc_year)],
        "last_sync": staging.last_sync("order_commit"),
    }
    if persona_name is None or flt is None:
        return {**base, "scope": [], "kpis": None, "rows": [], "total_rows": 0}

    # customer x item — what I promised and what I have firm
    lines = _dem._ledger(flt, acc_year, want)
    # item — the supply position and everyone else's claims
    items = {r["key"]: r for r in _comp._ledger(flt, flt_c, acc_year, want)}
    # item — the dated supply ladder
    sup = _comp.supply_inputs()
    prod = _prom._production_events(sup)
    po = _prom._po_arrivals()
    sched: dict = {}
    for r in staging.commit_schedule(sup["cutoff"]):
        sched.setdefault(_prom._key(r["item_key"]), []).append(
            {"date": r["due"], "qty": r["qty"]})
    w1_req, w2_req = _prom._required_dates(base["jc_from"], base["jc_to"])

    rows = []
    for ln in lines:
        k = _comp._key(ln["item"])
        it = items.get(k) or {}
        atp = float(it.get("atp_for_me") or 0)
        unprot = ln["unprotected"]
        shortfall = round(max(0.0, unprot - max(0.0, atp)), 1)
        required = w2_req if ln.get("week2", 0) > 0 else w1_req
        ctp = risk_date = None
        breaches = False
        if unprot > 0:
            w = _prom._walk(float(it.get("on_hand") or 0),
                            (prod.get(k) or []) + (po.get(k) or []),
                            sched.get(k) or [], today, unprot,
                            float(it.get("msl") or 0))
            ctp, risk_date, breaches = w["ctp"], w["risk_date"], w["breaches_msl"]
        cls = _cls(unprot, shortfall, ctp, required)
        rows.append({
            "key": f"{ln['customer_id']}|{k}",
            "item_key": k,
            "customer_id": ln["customer_id"], "customer": ln["customer"],
            "collector": ln["collector"], "mc_code": ln["mc_code"],
            "item": ln["item"], "item_code": ln["item_code"], "segment3": ln["segment3"],
            "projection": ln["projected"],
            "soc": round(ln["dispatched"] + ln["soc"], 1),
            "my_soc": ln["soc"], "dispatched": ln["dispatched"],
            "protected": ln["covered"], "unprotected": unprot,
            # firm orders for this customer+item committed BEFORE the cycle —
            # still owed, but they cannot protect a cycle they predate
            "backlog": ln.get("backlog", 0.0),
            # the promise can only be met by dipping below the safety level,
            # which is usually why ATP reads negative while a date still exists
            "breaches_msl": bool(breaches),
            "other_soc": round(float(it.get("firm_others") or 0), 1),
            "other_customers": int(it.get("other_customers") or 0),
            "on_hand": round(float(it.get("on_hand") or 0), 1),
            "atp": round(atp, 1),
            "incoming": round(float(it.get("incoming") or 0), 1),
            "incoming_date": it.get("incoming_date"),
            "shortfall": shortfall,
            "required": required,
            "commit_date": ctp,
            "delay_days": _prom._days(ctp, required) if (ctp and unprot > 0) else None,
            "risk_date": risk_date,
            "risk": cls,
            # nothing has happened against this line at all — no order, no
            # dispatch. These are the ones an executive can still act on.
            "silent": bool(ln.get("silent")),
        })

    # ── the caller's own order book, split by where each line is dated ───────
    # SOC only counts orders committed INSIDE the cycle. A book full of overdue
    # lines therefore shows as SOC ~ 0, which looks like "you have no orders"
    # unless the rest of the book is shown alongside it.
    my_book = staging.read_order_commit(flt_c)
    my_customers = {c["customer_id"] for c in my_book if c.get("customer_id") is not None}
    jc_from, jc_to = base["jc_from"] or "", base["jc_to"] or ""

    def _due(c):
        return str(c.get("resched_date") or c.get("sched_date") or "")

    book = {"lines": len(my_book), "in_cycle": 0.0, "in_cycle_lines": 0,
            "overdue": 0.0, "overdue_lines": 0, "later": 0.0, "later_lines": 0}
    for c in my_book:
        d, q = _due(c), float(c.get("balance") or 0)
        if jc_from and d and d < jc_from:
            book["overdue"] += q
            book["overdue_lines"] += 1
        elif jc_to and d and d > jc_to:
            book["later"] += q
            book["later_lines"] += 1
        else:
            book["in_cycle"] += q
            book["in_cycle_lines"] += 1
    for f in ("in_cycle", "overdue", "later"):
        book[f] = round(book[f], 1)

    # Headline — the tiles are exactly the row classes rolled up, so the colour a
    # user sees on a line is the tile its quantity landed in.
    projection = sum(r["projection"] for r in rows)
    protected = sum(r["protected"] for r in rows)
    unprot_tot = sum(r["unprotected"] for r in rows)
    critical = round(sum(r["unprotected"] for r in rows if r["risk"] == "critical"), 1)
    at_risk = round(sum(r["unprotected"] for r in rows if r["risk"] == "watch"), 1)
    action = [r for r in rows if r["risk"] != "safe"]
    action.sort(key=lambda r: ({"critical": 0, "watch": 1}.get(r["risk"], 2),
                               -r["shortfall"], -r["unprotected"]))

    kpis = {
        "projection": round(projection, 1),
        "soc": round(sum(r["soc"] for r in rows), 1),
        "protected": round(protected, 1),
        "backlog": round(sum(r["backlog"] for r in rows), 1),
        "backlog_lines": sum(1 for r in rows if r["backlog"] > 0),
        "below_msl": sum(1 for r in rows if r["breaches_msl"]),
        "book": book,
        "at_risk": at_risk,
        "critical": critical,
        "unprotected": round(unprot_tot, 1),
        "protection_pct": (round(100.0 * protected / projection, 1) if projection else None),
        "lines": len(rows),
        "action_lines": len(action),
        "customers": len({r["customer_id"] for r in rows}),
        "items": len({r["item_key"] for r in rows}),
        "worst_delay": max([r["delay_days"] for r in action
                            if r["delay_days"] is not None] or [0]),
        "no_date": sum(1 for r in action if r["unprotected"] > 0 and not r["commit_date"]),
        "buckets": [{"key": c, "label": lbl,
                     "lines": sum(1 for r in rows if r["risk"] == c),
                     "qty": round(sum(r["unprotected"] for r in rows if r["risk"] == c), 1)}
                    for c, lbl in RISK],
    }
    # UNCAPPED here; action_board() caps for the page, exports read all of it
    # ── item-level supply picture (one walk per item, need=0 -> the run-out
    # date is independent of what any one customer needs) ────────────────────
    unprot_by_item: dict = {}
    for r in rows:
        unprot_by_item[r["item_key"]] = unprot_by_item.get(r["item_key"], 0.0) + r["unprotected"]

    item_rows = []
    for k, it in items.items():
        supply = (prod.get(k) or []) + (po.get(k) or [])
        w = _prom._walk(float(it.get("on_hand") or 0), supply, sched.get(k) or [],
                        today, 0.0, float(it.get("msl") or 0))
        mine_unprot = round(unprot_by_item.get(k, 0.0), 1)
        atp = float(it.get("atp_for_me") or 0)
        item_rows.append({
            "key": k, "item": it.get("item"), "item_code": it.get("item_code"),
            "segment3": it.get("segment3"),
            "on_hand": round(float(it.get("on_hand") or 0), 1),
            "msl": round(float(it.get("msl") or 0), 1),
            "firm_others": round(float(it.get("firm_others") or 0), 1),
            "my_unprotected": mine_unprot,
            "incoming": round(float(it.get("incoming") or 0), 1),
            "incoming_date": it.get("incoming_date"),
            "atp": round(atp, 1),
            "exposure": round(max(0.0, mine_unprot - max(0.0, atp)), 1),
            "risk_date": w["risk_date"],
            "days_to_risk": _prom._days(w["risk_date"], today.isoformat()),
            "sources": sorted({e["source"] for e in supply}),
            "estimated": any(e["estimate"] for e in supply),
        })
    item_rows.sort(key=lambda r: (-r["exposure"], -r["my_unprotected"]))

    # ── who else holds firm orders on the items we are exposed on ────────────
    exposed_keys = [r["key"] for r in item_rows if r["exposure"] > 0][:400]
    competing_coll, competing_mc = [], []
    if exposed_keys:
        holders = [h for h in staging.commit_holders(exposed_keys, sup["cutoff"])
                   if h.get("customer_id") not in my_customers]
        for h in holders:
            h["balance"] = float(h.get("balance") or 0)
            h["item_key"] = _comp._key(h.get("item_key"))
        competing_coll = _comp._holders_rollup(holders, lambda h: h.get("collector"), "collector")[:60]
        competing_mc = _comp._holders_rollup(holders, lambda h: h.get("mc_code"), "mc_code")[:60]

    # where the exposure sits — same ledger, rolled up three ways
    by_collector = _dem._rollup(lines, lambda r: r["collector"], "collector")
    by_item = _dem._rollup(lines, lambda r: r["item"], "item",
                           lambda r: {"item_code": r["item_code"], "segment3": r["segment3"]})
    by_customer = _dem._rollup(lines, lambda r: r["customer"], "customer",
                               lambda r: {"collector": r["collector"], "mc_code": r["mc_code"]})

    return {**base, "scope": _comp._scope_summary(persona_name, stype, mine),
            "kpis": kpis, "rows": action, "total_rows": len(action),
            "all_lines": len(rows),
            "by_collector": by_collector[:60],
            "by_item": by_item[:200],
            "by_customer": by_customer[:200],
            "items": item_rows[:200],
            "competing_by_collector": competing_coll,
            "competing_by_mc": competing_mc}


_CACHE: dict = {}


def action_board(username=None, email=None, admin=False, persona=None, jc=None) -> dict:
    stamp = "|".join([
        str((staging.last_sync("projection_customer") or {}).get("finished_at") or ""),
        str((staging.last_sync("order_commit") or {}).get("finished_at") or ""),
        str((staging.last_sync("stock_details") or {}).get("finished_at") or ""),
        str(date.today()), f"v{_PAYLOAD_V}"])
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    ck = (username or "", email or "", bool(admin), persona or "", jc or 0)
    if ck not in _CACHE:
        _CACHE[ck] = build(username, email, admin, persona, jc)
    full = _CACHE[ck]
    return {**full, "rows": full["rows"][:_ROW_CAP]}


def item_supply(item_key: str, username=None, email=None, admin=False,
                persona=None, jc=None) -> dict:
    """One item's full supply picture: the dated ladder behind its promise date,
    the committed orders burning it down, and where the stock physically sits.

    Customers outside the caller's scope are rolled up by collector and market
    circle — the same permission rule the rest of the board follows."""
    p = action_board(username, email, admin, persona, jc)
    if p.get("kpis") is None:
        return {"found": False, "item": item_key}
    k = _comp._key(item_key)
    row = next((r for r in (p.get("items") or []) if r["key"] == k), None)
    if row is None:
        return {"found": False, "item": item_key}

    _pn, _st, _mn, _flt, flt_c = _comp.resolve_scope(username, email, admin, persona)
    sup = _comp.supply_inputs()
    supply = (_prom._production_events(sup).get(k) or []) + (_prom._po_arrivals().get(k) or [])
    sched = [{"date": r["due"], "qty": r["qty"], "lines": r["lines_"]}
             for r in staging.commit_schedule(sup["cutoff"]) if _comp._key(r["item_key"]) == k]
    w = _prom._walk(row["on_hand"], supply, sched, date.today(), 0.0, row["msl"])

    my_customers = {c["customer_id"] for c in staging.read_order_commit(flt_c or {})
                    if c.get("customer_id") is not None}
    show_names = bool(admin or _comp.SHOW_ALL_HOLDERS)
    named, hidden = [], {}
    for h in staging.commit_holders([k], sup["cutoff"]):
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

    # which of the caller's own customers are projecting this item
    mine_lines = [{"customer": r["customer"], "projection": r["projection"],
                   "soc": r["soc"], "unprotected": r["unprotected"],
                   "commit_date": r["commit_date"], "risk": r["risk"]}
                  for r in (p.get("rows") or []) if r["item_key"] == k]
    mine_lines.sort(key=lambda r: -r["unprotected"])

    return {"found": True, "item": row["item"], "key": k, "row": row,
            "today": p.get("today"), "jc_label": p.get("jc_label"),
            "show_names": show_names,
            "sources": sorted(supply, key=lambda e: e["date"]),
            "ladder": w["ladder"],
            "holders": named[:200], "grouped": grouped[:60],
            "by_org": sorted(({"org": o, "qty": round(q, 1)}
                              for o, q in (sup["by_org"].get(k) or {}).items()),
                             key=lambda x: -x["qty"])[:40],
            "my_lines": mine_lines[:60]}


def scoped_rows(username=None, email=None, admin=False, persona=None, jc=None):
    """(page payload, every action row — UNCAPPED) for exports."""
    action_board(username, email, admin, persona, jc)          # populate the cache
    ck = (username or "", email or "", bool(admin), persona or "", jc or 0)
    full = _CACHE.get(ck) or {}
    return {**full, "rows": (full.get("rows") or [])[:_ROW_CAP]}, full.get("rows") or []


__all__ = ["action_board", "build", "item_supply", "scoped_rows", "RISK", "_RISK_LABEL"]
