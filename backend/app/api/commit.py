"""Commitment Risk — date-risk view over open committed order lines.

Serves from ``stg_order_commit`` (worker-synced, see db/migrate_commit.sql).
Persona scoping reuses the My-Dashboard machinery (stg_user_scope), so every
persona sees only their own orders and the admin View-as switcher just works.

The whole page is about dates, and CRM has no literal rush/emergency flags, so
the classes are DERIVED — the business definitions agreed for this page:

  overdue7 / overdue   the CURRENT commitment (resched_date) has passed with
                       balance still to dispatch (worst: > 7 days late)
  today / d2           due today / within 48h and undispatched  -> "rush"
  week / later         due in 3-7 days / beyond

Extra signals: lines PUSHED past their original commitment (sched < resched,
with the reschedule reason — "Stock Not Available" is a supply-side failure),
and a supply check against the latest saved JC plan's production schedule
(an urgent line whose item has no production finishing before its commitment
date is flagged supply-risk).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..integration import staging
from ..integration.planning_filter import _squash
from .dashboard import _norm, _pick_persona, _scope_summary, _segment_grants

# bump when the payload shape changes (stale caches are rebuilt)
_PAYLOAD_V = 3
# Detail rows in the page payload are capped PER RISK CLASS, not globally —
# a flat cap filled up with the most-overdue lines and left the rush / later
# buckets empty on wide scopes (blank charts and tables). Exports are uncapped.
_BUCKET_CAP = 150

# More than half the pending book is overdue by MONTHS — orders that were never
# closed rather than commitments anyone is still chasing. They get their own
# class so they cannot drown the lines that are actually actionable.
STALE_DAYS = 90

BUCKETS = [
    ("stale", f"Overdue > {STALE_DAYS} days"),
    ("overdue7", "Overdue > 7 days"),
    ("overdue", "Overdue"),
    ("today", "Due today"),
    ("d2", "Due in 1–2 days"),
    ("week", "Due this week"),
    ("later", "Later"),
    ("nodate", "No commitment date"),
]


def _bucket(days) -> str:
    if days is None:
        return "nodate"
    if days < -STALE_DAYS:
        return "stale"
    if days < -7:
        return "overdue7"
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days <= 2:
        return "d2"
    if days <= 7:
        return "week"
    return "later"


def _commit_flt(persona: str, grants: list[dict]):
    """(scope_type, persona's grant rows, filter for staging.read_order_commit).
    Collector conditions use NAMES — the pending-order feed carries no ids."""
    mine = [g for g in grants if g["persona"] == persona]
    stype = mine[0]["scope_type"] if mine else "segment"
    if stype == "market_circle":
        codes = sorted({g["mc_code"] for g in mine if g.get("mc_code")})
        return stype, mine, ({"mc_codes": codes} if codes else None)
    if stype == "collector":
        names = sorted({g["collector_name"] for g in mine if g.get("collector_name")})
        return stype, mine, ({"collectors": names} if names else None)
    if stype == "customer":
        ids = sorted({g["customer_id"] for g in mine if g.get("customer_id")})
        return stype, mine, ({"customer_ids": ids} if ids else None)

    # deepest-level segment matching, same rule as the dashboard — but the
    # collector restriction is carried as names here
    names_of = {}
    for g in mine:
        if g.get("collector_id") is not None and g.get("collector_name"):
            names_of.setdefault((g.get("segment2"), g.get("segment3"), g.get("segment4")),
                                set()).add(g["collector_name"])
    sg = []
    for g in _segment_grants(mine):
        level, value = next(((lv, g[k]) for lv, k in
                             (("segment4", "s4"), ("segment3", "s3"), ("segment2", "s2"))
                             if g[k]), (None, None))
        if not level:
            continue
        coll = None if g["collectors"] is None else \
            sorted(names_of.get((g["s2"], g["s3"], g["s4"]), set()))
        sg.append({"level": level, "value": value, "collectors": coll or None if coll is not None else None})
    return stype, mine, ({"segment_grants": sg} if sg else None)


# ── supply check against the latest saved JC plan ────────────────────────────

_supply_cache: dict = {}


def _supply_map() -> tuple[dict, int | None]:
    """{squashed item name: warehouse-available ISO date} from the newest saved
    JC plan's production schedule (+ the standard receipt lead). Empty when no
    plan exists or the schedule can't build — the page degrades gracefully."""
    try:
        from ..integration import mysql_db as _mysql
        from ..integration import planning_settings as _ps
        from .live import _production_schedule
        plans = _mysql.list_jc_plans()
        if not plans:
            return {}, None
        pid = plans[0].get("plan_id")
        if _supply_cache.get("pid") == pid:
            return _supply_cache["map"], pid
        std = int(_ps.load().get("receipt_std_lead_days", 3))
        out = {}
        for j in (_production_schedule(pid) or {}).get("jobs") or []:
            end = j.get("end")
            k = _squash(j.get("item"))
            if not k or not end:
                continue
            try:
                avail = (date.fromisoformat(str(end)[:10]) + timedelta(days=std)).isoformat()
            except ValueError:
                continue
            if k not in out or avail < out[k]:
                out[k] = avail
        _supply_cache.update({"pid": pid, "map": out})
        return out, pid
    except Exception:   # noqa: BLE001
        return {}, None


# ── the page payload ─────────────────────────────────────────────────────────

def _classify(rows: list[dict], today: date, supply: dict) -> list[dict]:
    for r in rows:
        rd = r.get("resched_date")
        try:
            days = (date.fromisoformat(rd) - today).days if rd else None
        except ValueError:
            days = None
        r["days"] = days
        r["bucket"] = _bucket(days)
        r["pushed"] = bool(r.get("sched_date") and rd and rd > r["sched_date"])
        # we committed to a different date than the customer asked for
        cr = r.get("cust_req_date")
        r["off_request"] = bool(cr and rd and cr != rd)
        avail = supply.get(_squash(r.get("item_name")))
        r["supply_date"] = avail
        # urgent + the plan cannot produce it before the commitment
        r["supply_risk"] = bool(r["bucket"] in ("overdue7", "overdue", "today", "d2")
                                and (avail is None or (rd and avail > rd)))
    return rows


def build_payload(rows: list[dict], today: date, supply_plan) -> dict:
    urgency = {k: i for i, (k, _) in enumerate(BUCKETS)}
    buckets = []
    for key, label in BUCKETS:
        sub = [r for r in rows if r["bucket"] == key]
        if sub:
            buckets.append({"key": key, "label": label, "lines": len(sub),
                            "kg": round(sum(r["balance"] for r in sub))})

    timeline = [{"key": "overdue", "label": "Overdue", "overdue": True,
                 "kg": round(sum(r["balance"] for r in rows if (r["days"] or 0) < 0 and r["days"] is not None)),
                 "lines": sum(1 for r in rows if r["days"] is not None and r["days"] < 0)}]
    for i in range(0, 15):
        d = today + timedelta(days=i)
        sub = [r for r in rows if r["days"] == i]
        timeline.append({"key": d.isoformat(), "label": d.strftime("%d %b"),
                         "overdue": False, "kg": round(sum(r["balance"] for r in sub)),
                         "lines": len(sub)})

    pushed = [r for r in rows if r["pushed"]]
    reasons: dict = {}
    for r in pushed:
        k = r.get("resched_reason") or "No reason recorded"
        a = reasons.setdefault(k, {"reason": k, "lines": 0, "kg": 0.0})
        a["lines"] += 1
        a["kg"] += r["balance"]
    reason_rows = sorted(({**a, "kg": round(a["kg"])} for a in reasons.values()),
                         key=lambda x: -x["lines"])

    detail = sorted(rows, key=lambda r: (urgency.get(r["bucket"], 9),
                                         r["days"] if r["days"] is not None else 999,
                                         -r["balance"]))
    detail = [{k: r.get(k) for k in (
        "order_ref", "order_no", "soc_date", "customer_name", "collector", "mc_code",
        "item_code", "item_name", "inv_org", "balance", "qty",
        "sched_date", "resched_date", "cust_req_date", "resched_reason", "wh_comments",
        "executive", "days", "bucket", "pushed", "off_request",
        "supply_date", "supply_risk")} for r in detail]
    capped, used = [], {}
    for r in detail:
        b = r["bucket"]
        if used.get(b, 0) < _BUCKET_CAP:
            capped.append(r)
            used[b] = used.get(b, 0) + 1

    urgent = [r for r in rows if r["bucket"] in ("overdue7", "overdue", "today", "d2")]
    stale = [r for r in rows if r["bucket"] == "stale"]
    return {
        "kpis": {
            "lines": len(rows), "kg": round(sum(r["balance"] for r in rows)),
            "overdue_lines": sum(1 for r in rows if r["bucket"] in ("overdue7", "overdue")),
            "overdue_kg": round(sum(r["balance"] for r in rows if r["bucket"] in ("overdue7", "overdue"))),
            "stale_lines": len(stale), "stale_kg": round(sum(r["balance"] for r in stale)),
            "off_request_lines": sum(1 for r in rows if r.get("off_request")),
            "rush_lines": sum(1 for r in rows if r["bucket"] in ("today", "d2")),
            "rush_kg": round(sum(r["balance"] for r in rows if r["bucket"] in ("today", "d2"))),
            "pushed_lines": len(pushed),
            "supply_risk_lines": sum(1 for r in urgent if r["supply_risk"] and r["supply_date"] is not None),
        },
        "buckets": buckets,
        "timeline": timeline,
        "reasons": reason_rows,
        "rows": capped,
        "total_rows": len(rows),
        "supply_plan": supply_plan,
    }


_CACHE: dict = {}


def resolve_scope(username=None, email=None, admin=False, persona=None):
    """(persona, stype, mine, flt) for one caller — shared by the page payload
    and the Excel export so both see exactly the same order lines."""
    if admin:
        return "Admin", "", [], {}
    grants = staging.read_user_scope(email=email or None, username=username or None) \
        if (email or username) else []
    if not (persona and any(g["persona"] == persona for g in grants)):
        persona = _pick_persona(grants)
    if not persona:
        return None, "", [], None
    stype, mine, flt = _commit_flt(persona, grants)
    return persona, stype, mine, flt


def scoped_rows(username=None, email=None, admin=False, persona=None):
    """(persona, stype, mine, classified rows — UNCAPPED) for exports."""
    persona, stype, mine, flt = resolve_scope(username, email, admin, persona)
    supply, _pid = _supply_map()
    rows = _classify(staging.read_order_commit(flt), date.today(), supply) \
        if flt is not None else []
    return persona, stype, mine, rows


def commit_risk(username: str | None = None, email: str | None = None,
                admin: bool = False, persona: str | None = None) -> dict:
    today = date.today()
    stamp = f"{(staging.last_sync('order_commit') or {}).get('finished_at') or ''}|{today}|v{_PAYLOAD_V}"
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    key = (username or "", email or "", bool(admin), persona or "")
    if key in _CACHE:
        return _CACHE[key]

    persona, stype, mine, flt = resolve_scope(username, email, admin, persona)
    base = {"v": _PAYLOAD_V, "persona": persona,
            "today": today.isoformat(), "last_sync": staging.last_sync("order_commit")}
    if persona is None:
        return {**base, "scope": [], "kpis": None, "buckets": [], "timeline": [],
                "reasons": [], "rows": [], "total_rows": 0, "supply_plan": None}

    supply, plan_id = _supply_map()
    rows = _classify(staging.read_order_commit(flt), today, supply) if flt is not None else []
    payload = {**base,
               "scope": _scope_summary(persona, stype, mine),
               "user_name": mine[0].get("user_name") if mine else None,
               **build_payload(rows, today, plan_id)}
    _CACHE[key] = payload
    return payload


__all__ = ["commit_risk", "scoped_rows", "build_payload", "BUCKETS"]
