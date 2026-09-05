"""Demand Protection — is a projection actually covered by firm demand?

Phase 1 of the Projection -> SOC -> Supply -> Commitment work. It answers the
two questions with the most management value and the least modelling risk:

  Protected vs unprotected projection   how much of what I said I would sell is
                                        backed by a real order or already shipped
  Projection-to-SOC conversion          the same figure as a percentage, per
                                        collector / customer / item / cycle

THE ONE RULE THAT MATTERS
-------------------------
Cover is NOT "an open SOC exists". A projection that converted to an order and
already shipped has no open SOC line left — the order is closed. Measuring cover
against open orders alone therefore counts every SUCCESSFUL sale as a failure.
Measured on JC6 at 75% elapsed: open-SOC-only reports 9.6% converted, while
dispatch + open SOC reports 32.4%. So:

    covered = min(projection, dispatched_in_cycle + open_SOC_due_in_cycle)

Firm demand is attributed to the JC its CURRENT commitment date falls in — an
order promised for October does not protect a September projection. Anything
already overdue when the cycle opens is reported as ``backlog``, visible but
never counted as cover.

CRM's own jc{n}_qty_achieved column is deliberately NOT used: it reports
3,323,563 against 1,322,672 projected for JC6 (251%), so it is measuring
something other than this cycle's achievement.

Persona scoping reuses the My-Dashboard machinery (stg_user_scope), so every
persona sees only their own customers and the admin View-as switcher just works.
"""
from __future__ import annotations

from ..integration import jc_calendar as _jc
from ..integration import msl as _msl
from ..integration import staging
from .dashboard import _pick_persona, _scope_flt, _scope_summary

# bump when the payload shape changes (stale caches are rebuilt)
_PAYLOAD_V = 1
# detail rows returned to the page; exports read the same ledger uncapped
_ROW_CAP = 500
# a collector whose whole projection is rounding dust would otherwise top the
# league table on a 0% score
_LEAGUE_MIN = 1.0


def _key(name) -> str:
    return str(name or "").strip().upper()


def _pct(part: float, whole: float):
    return round(100.0 * part / whole, 1) if whole > 0 else None


# ── JC resolution ────────────────────────────────────────────────────────────

def _jc_entry(acc_year: str, jc: int) -> dict | None:
    return next((j for j in _jc.all_jcs()
                 if j["fy"] == acc_year and int(j["jc"]) == int(jc)), None)


def _cube_index(acc_year: str, jc: int) -> int | None:
    """Which jc_index of the dispatch cube holds this JC (None = outside the
    13-JC window, e.g. a future planning JC that has not shipped anything)."""
    for i, j in enumerate(_msl.jc_window()):
        if j["fy"] == acc_year and int(j["jc"]) == int(jc):
            return i
    return None


# ── the ledger ───────────────────────────────────────────────────────────────

def _ledger(flt: dict, acc_year: str, jc: int) -> list[dict]:
    """One row per (customer, item) in scope: projected, dispatched, open SOC,
    covered, unprotected. The join key is the customer plus the NORMALISED item
    name — an item can carry several codes, and the plan stores a name while
    orders and the dispatch cube store a code."""
    entry = _jc_entry(acc_year, jc)
    if flt is None or not entry:
        return []
    rows = staging.read_projection_customer(flt, acc_year, jc)
    if not rows:
        return []

    # collapse to (customer, item): the same pair can appear under two
    # collectors, and the order/dispatch side knows nothing about collectors —
    # counting it twice would let one order cover two projection lines.
    agg: dict = {}
    for r in rows:
        k = (r.get("customer_id"), _key(r.get("item_name")))
        a = agg.get(k)
        if a is None:
            a = agg[k] = {
                "customer_id": r.get("customer_id"), "customer": r.get("customer_name"),
                "collector": r.get("collector"), "collector_id": r.get("collector_id"),
                "mc_code": r.get("mc_code"), "item": r.get("item_name"),
                "item_code": r.get("item_code"), "segment2": r.get("segment2"),
                "segment3": r.get("segment3"), "segment4": r.get("segment4"),
                "projected": 0.0, "week1": 0.0, "week2": 0.0, "next1": 0.0, "next2": 0.0,
            }
        a["projected"] += r["current_q"]
        a["week1"] += r["week1_q"]
        a["week2"] += r["week2_q"]
        a["next1"] += r["next1_q"]
        a["next2"] += r["next2_q"]

    cust_ids = {k[0] for k in agg if k[0] is not None}
    soc = {(r["customer_id"], r["item_key"]): r
           for r in staging.ledger_open_soc(cust_ids, entry["from"], entry["to"])}
    disp = {(r["customer_id"], r["item_key"]): r["qty"]
            for r in staging.ledger_dispatch(cust_ids, _cube_index(acc_year, jc))}

    out = []
    for k, a in agg.items():
        s = soc.get(k) or {}
        a["dispatched"] = round(disp.get(k, 0.0), 3)
        a["soc"] = round(float(s.get("in_jc") or 0), 3)
        a["backlog"] = round(float(s.get("backlog") or 0), 3)
        a["soc_lines"] = int(s.get("lines_") or 0)
        if not a["item_code"]:
            a["item_code"] = s.get("item_code")
        firm = a["dispatched"] + a["soc"]
        a["covered"] = round(min(a["projected"], firm), 3)
        a["unprotected"] = round(max(0.0, a["projected"] - firm), 3)
        a["over"] = round(max(0.0, firm - a["projected"]), 3)
        a["pct"] = _pct(a["covered"], a["projected"])
        a["silent"] = firm <= 0
        a["projected"] = round(a["projected"], 3)
        out.append(a)
    return out


def _totals(rows: list[dict]) -> dict:
    projected = sum(r["projected"] for r in rows)
    covered = sum(r["covered"] for r in rows)
    return {
        "projected": round(projected, 1),
        "dispatched": round(sum(r["dispatched"] for r in rows), 1),
        "soc": round(sum(r["soc"] for r in rows), 1),
        "backlog": round(sum(r["backlog"] for r in rows), 1),
        "protected": round(covered, 1),
        "unprotected": round(sum(r["unprotected"] for r in rows), 1),
        "over": round(sum(r["over"] for r in rows), 1),
        "protection_pct": _pct(covered, projected),
        "lines": len(rows),
        "customers": len({r["customer_id"] for r in rows}),
        "items": len({_key(r["item"]) for r in rows}),
        "silent_lines": sum(1 for r in rows if r["silent"]),
        "silent_qty": round(sum(r["projected"] for r in rows if r["silent"]), 1),
        "full_lines": sum(1 for r in rows if r["unprotected"] <= 0),
    }


def _rollup(rows: list[dict], keyfn, label: str, extra=None) -> list[dict]:
    """Group the ledger and re-derive the percentage from the group totals
    (never average the per-line percentages — a 10 KG line would weigh the
    same as a 10,000 KG one)."""
    agg: dict = {}
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        a = agg.get(k)
        if a is None:
            a = agg[k] = {label: k, "projected": 0.0, "dispatched": 0.0, "soc": 0.0,
                          "protected": 0.0, "unprotected": 0.0, "lines": 0,
                          "customers": set(), "silent_lines": 0}
            if extra:
                a.update(extra(r))
        a["projected"] += r["projected"]
        a["dispatched"] += r["dispatched"]
        a["soc"] += r["soc"]
        a["protected"] += r["covered"]
        a["unprotected"] += r["unprotected"]
        a["lines"] += 1
        a["silent_lines"] += 1 if r["silent"] else 0
        a["customers"].add(r["customer_id"])
    out = []
    for a in agg.values():
        a["customers"] = len(a["customers"])
        a["pct"] = _pct(a["protected"], a["projected"])
        for f in ("projected", "dispatched", "soc", "protected", "unprotected"):
            a[f] = round(a[f], 1)
        out.append(a)
    out.sort(key=lambda x: -x["unprotected"])
    return out


# ── payload ──────────────────────────────────────────────────────────────────

_CACHE: dict = {}


def resolve_scope(username=None, email=None, admin=False, persona=None):
    """(persona, stype, mine, flt) — shared by the page payload and the export
    so both see exactly the same projection lines."""
    if admin:
        return "Admin", "", [], {}
    grants = staging.read_user_scope(email=email or None, username=username or None) \
        if (email or username) else []
    if not (persona and any(g["persona"] == persona for g in grants)):
        persona = _pick_persona(grants)
    if not persona:
        return None, "", [], None
    stype, mine, flt = _scope_flt(persona, grants)
    return persona, stype, mine, flt


def scoped_ledger(username=None, email=None, admin=False, persona=None, jc=None):
    """(persona, stype, mine, acc_year, jc, ledger rows — UNCAPPED) for exports."""
    persona, stype, mine, flt = resolve_scope(username, email, admin, persona)
    acc_year, jc = _context(jc)
    rows = _ledger(flt, acc_year, jc) if flt is not None else []
    return persona, stype, mine, acc_year, jc, rows


def _context(jc=None) -> tuple[str, int]:
    """(acc_year, jc) to report on — the planning JC unless one is asked for."""
    entry = _jc.planning_jc_entry() or _jc.current_jc_entry() or {}
    acc_year = entry.get("fy") or ""
    want = int(jc) if jc else int(entry.get("jc") or 0)
    return acc_year, want


def demand_protection(username: str | None = None, email: str | None = None,
                      admin: bool = False, persona: str | None = None,
                      jc: int | None = None) -> dict:
    acc_year, want_jc = _context(jc)
    stamp = "|".join([
        str((staging.last_sync("projection_customer") or {}).get("finished_at") or ""),
        str((staging.last_sync("order_commit") or {}).get("finished_at") or ""),
        str((staging.last_sync("dispatch_scope") or {}).get("finished_at") or ""),
        f"v{_PAYLOAD_V}"])
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    key = (username or "", email or "", bool(admin), persona or "", want_jc)
    if key in _CACHE:
        return _CACHE[key]

    persona, stype, mine, flt = resolve_scope(username, email, admin, persona)
    entry = _jc_entry(acc_year, want_jc) or {}
    staged = staging.projection_customer_jcs(acc_year)
    base = {
        "v": _PAYLOAD_V, "persona": persona, "acc_year": acc_year,
        "jc": want_jc, "jc_label": entry.get("label") or f"JC{want_jc}",
        "jc_from": entry.get("from"), "jc_to": entry.get("to"),
        "has_dispatch": _cube_index(acc_year, want_jc) is not None,
        "jcs": [{"jc": j, "label": f"JC{j}"} for j in staged],
        "last_sync": staging.last_sync("projection_customer"),
    }
    if persona is None or flt is None:
        return {**base, "scope": [], "kpis": None, "by_customer": [], "by_item": [],
                "by_collector": [], "trend": [], "rows": [], "total_rows": 0}

    rows = _ledger(flt, acc_year, want_jc)

    # trend: the same measure for every staged cycle of the year
    trend = []
    for j in staged:
        e = _jc_entry(acc_year, j)
        t = _totals(_ledger(flt, acc_year, j)) if e else None
        if t and t["projected"] > 0:
            trend.append({"jc": j, "label": f"JC{j}", "projected": t["projected"],
                          "protected": t["protected"], "unprotected": t["unprotected"],
                          "dispatched": t["dispatched"], "soc": t["soc"],
                          "pct": t["protection_pct"]})

    by_customer = _rollup(rows, lambda r: r["customer"], "customer",
                          lambda r: {"customer_id": r["customer_id"],
                                     "collector": r["collector"], "mc_code": r["mc_code"]})
    by_item = _rollup(rows, lambda r: r["item"], "item",
                      lambda r: {"item_code": r["item_code"], "segment3": r["segment3"]})
    by_collector = [c for c in _rollup(rows, lambda r: r["collector"], "collector")
                    if c["projected"] >= _LEAGUE_MIN]

    detail = sorted(rows, key=lambda r: (-r["unprotected"], -r["projected"]))
    payload = {
        **base,
        "scope": _scope_summary(persona, stype, mine),
        "kpis": _totals(rows),
        "by_customer": by_customer[:200],
        "by_item": by_item[:200],
        "by_collector": by_collector,
        "trend": trend,
        "rows": detail[:_ROW_CAP],
        "total_rows": len(rows),
    }
    _CACHE[key] = payload
    return payload


__all__ = ["demand_protection", "resolve_scope", "scoped_ledger", "_ledger", "_totals"]
