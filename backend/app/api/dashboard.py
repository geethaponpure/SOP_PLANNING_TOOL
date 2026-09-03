"""My Dashboard — permission-scoped sales/dispatch view (serve-from-DB only).

Resolves the logged-in user's data grants from ``stg_user_scope`` (synced from
six CRM mapping tables, see db/migrate_user_scope.sql), turns them into a SQL
filter over the staged dispatch cube (``stg_dispatch_scope``), and returns the
compact datasets the page charts. All heavy lifting happens as indexed MySQL
aggregates via ``staging.dashboard_datasets`` — a page load is four small
queries, never a full-cube haul into Python.

Personas and how their scope filters the cube:
  Sales Executive      mc_code   in his market circles
  Branch Manager       collector_id in his collector(s)
  Regional Manager     collector_id in his collectors
  Technical Executive  customer_id  in his customers
  Technical Mgr/Head   item's segment4 matches the grant (+ collector list)
  Business Head        segment3-level grant (+ collector list)
  Division Head        whole segment2 (division)
  Admin                everything (User-Master admins / bootstrap mode)
"""
from __future__ import annotations

from ..integration import msl as _msl
from ..integration import staging

# broadest scope first — a user holding several personas gets the widest view
_PERSONA_PRIORITY = ["Division Head", "Business Head", "Technical Head",
                     "Technical Manager", "Regional Manager", "Branch Manager",
                     "Sales Executive", "Technical Executive"]

_CUBE_MAX_COLLECTORS = 12   # cube buckets beyond these become "Other" (keeps the
_CUBE_MAX_SEGMENTS = 10     # client-side cross-filter payload small)


def _pick_persona(grants: list[dict]) -> str | None:
    have = {g["persona"] for g in grants}
    return next((p for p in _PERSONA_PRIORITY if p in have), None)


def _segment_grants(rows: list[dict]) -> list[dict]:
    """Group segment-scope rows to (s2, s3, s4) -> collector set (None = all)."""
    agg: dict = {}
    for g in rows:
        key = (g.get("segment2"), g.get("segment3"), g.get("segment4"))
        cur = agg.setdefault(key, set())
        if cur is None:
            continue
        if g.get("collector_id") is None:
            agg[key] = None          # any all-collector row unrestricts the key
        else:
            cur.add(g["collector_id"])
    return [{"s2": k[0], "s3": k[1], "s4": k[2], "collectors": v}
            for k, v in agg.items()]


def _scope_flt(persona: str, grants: list[dict]):
    """(scope_type, persona's grant rows, filter dict for dashboard_datasets).
    The filter dict is None when the grants resolve to nothing (no data)."""
    mine = [g for g in grants if g["persona"] == persona]
    stype = mine[0]["scope_type"] if mine else "segment"
    if stype == "market_circle":
        codes = sorted({g["mc_code"] for g in mine if g.get("mc_code")})
        return stype, mine, ({"mc_codes": codes} if codes else None)
    if stype == "collector":
        ids = sorted({g["collector_id"] for g in mine if g.get("collector_id") is not None})
        return stype, mine, ({"collector_ids": ids} if ids else None)
    if stype == "customer":
        ids = sorted({g["customer_id"] for g in mine if g.get("customer_id") is not None})
        return stype, mine, ({"customer_ids": ids} if ids else None)

    # Segment scope filters on the DEEPEST level the grant specifies (s4 for
    # Technical Mgr/Head, s3 for Business Head, s2 for Division Head). Parent
    # levels are ignored: CRM keeps them inconsistent between the grant tables
    # and ItemCategories (e.g. a grant says s3='Textile Pure' while most items
    # in that s4 carry s3='Textile'), and the persona's scope IS its own level.
    sg = []
    for g in _segment_grants(mine):
        level, value = next(((lv, g[k]) for lv, k in
                             (("segment4", "s4"), ("segment3", "s3"), ("segment2", "s2"))
                             if g[k]), (None, None))
        if not level:
            continue
        sg.append({"level": level, "value": value,
                   "collector_ids": sorted(g["collectors"]) if g["collectors"] else None})
    return stype, mine, ({"segment_grants": sg} if sg else None)


def _scope_summary(persona: str, stype: str, mine: list[dict]) -> list[str]:
    if persona == "Admin":
        return ["Full access — all divisions, collectors and customers"]
    if stype == "market_circle":
        codes = sorted({g["mc_code"] for g in mine if g.get("mc_code")})
        return [f"Market circle{'s' if len(codes) > 1 else ''}: {', '.join(codes)}"]
    if stype == "collector":
        names = sorted({g.get("collector_name") or str(g.get("collector_id")) for g in mine})
        shown = ", ".join(names[:6]) + (f" +{len(names) - 6} more" if len(names) > 6 else "")
        return [f"Collector{'s' if len(names) > 1 else ''}: {shown}"]
    if stype == "customer":
        return [f"{len({g['customer_id'] for g in mine if g.get('customer_id')}):,} customers assigned"]
    out = []
    for g in _segment_grants(mine):
        leaf = g["s4"] or g["s3"] or g["s2"] or "—"
        coll = "all collectors" if g["collectors"] is None else \
            f"{len(g['collectors'])} collector{'s' if len(g['collectors']) > 1 else ''}"
        out.append(f"{leaf} ({coll})")
    out.sort()
    if len(out) > 8:
        out = out[:8] + [f"+{len(out) - 8} more segments"]
    return [f"Segments: {'; '.join(out)}"] if out else []


def _empty_datasets() -> dict:
    return {"kpis": {"qty": 0, "value": 0, "customers": 0, "items": 0,
                     "last_jc_qty": 0, "prev_jc_qty": 0},
            "cube": [], "top_items": [], "top_customers": []}


def _assemble(ds: dict, n_jc: int) -> dict:
    """Shape the SQL aggregates for the page: bucket long tails into 'Other',
    derive totals/KPIs from the grouped cube (bucketing preserves sums)."""
    rows = [{"jc": int(r["jc"]), "collector": r["collector"], "segment": r["segment"],
             "qty": float(r["qty"] or 0), "value": float(r["value_"] or 0)}
            for r in ds["cube"] if 0 <= int(r["jc"]) < n_jc]

    coll_q: dict = {}
    seg_q: dict = {}
    jc_q = [0.0] * n_jc
    tot_q = tot_v = 0.0
    for r in rows:
        coll_q[r["collector"]] = coll_q.get(r["collector"], 0.0) + r["qty"]
        seg_q[r["segment"]] = seg_q.get(r["segment"], 0.0) + r["qty"]
        jc_q[r["jc"]] += r["qty"]
        tot_q += r["qty"]
        tot_v += r["value"]
    top_colls = {c for c, _ in sorted(coll_q.items(), key=lambda x: -x[1])[:_CUBE_MAX_COLLECTORS]}
    top_segs = {c for c, _ in sorted(seg_q.items(), key=lambda x: -x[1])[:_CUBE_MAX_SEGMENTS]}

    cube_agg: dict = {}
    for r in rows:
        key = (r["jc"],
               r["collector"] if r["collector"] in top_colls else "Other",
               r["segment"] if r["segment"] in top_segs else "Other")
        c = cube_agg.setdefault(key, [0.0, 0.0])
        c[0] += r["qty"]
        c[1] += r["value"]
    cube = [{"jc": k[0], "collector": k[1], "segment": k[2],
             "qty": round(qv[0], 1), "value": round(qv[1])}
            for k, qv in cube_agg.items()]

    tops = {}
    for key, src in (("top_items", ds["top_items"]), ("top_customers", ds["top_customers"])):
        tops[key] = [{**({"code": r["code"]} if "code" in r else {}),
                      "name": r.get("name") or "—",
                      "qty": round(float(r["qty"] or 0), 1),
                      "value": round(float(r["value_"] or 0))} for r in src]

    t = ds.get("totals") or {}
    return {
        "kpis": {"qty": round(tot_q), "value": round(tot_v),
                 "customers": int(t.get("customers") or 0), "items": int(t.get("items") or 0),
                 "last_jc_qty": round(jc_q[-1]) if n_jc else 0,
                 "prev_jc_qty": round(jc_q[-2]) if n_jc > 1 else 0},
        "cube": cube,
        "top_items": tops["top_items"],
        "top_customers": tops["top_customers"],
    }


def persona_users() -> dict:
    """The admin 'View as' switcher: every persona with its mapped users (from
    stg_user_scope), ordered broadest persona first."""
    groups: dict = {}
    for r in staging.read_scope_users():
        groups.setdefault(r["persona"], []).append(
            {"username": r["username"], "user_name": r.get("user_name") or r["username"],
             "n_grants": int(r.get("n_grants") or 0)})
    order = {p: i for i, p in enumerate(_PERSONA_PRIORITY)}
    return {"personas": [
        {"persona": p, "users": groups[p]}
        for p in sorted(groups, key=lambda x: order.get(x, 99))
    ]}


# Per-process response cache, valid for one sync generation: cleared whenever
# dispatch_scope's last-sync stamp changes. Keeps repeat loads (and the admin
# 'View as' flipping back and forth) instant between syncs.
_CACHE: dict = {}


def my_dashboard(username: str | None = None, email: str | None = None,
                 admin: bool = False, persona: str | None = None) -> dict:
    """The whole page payload for one user (all reads from MySQL staging).
    ``persona`` forces that persona's grants when the user holds several
    (used by the admin 'View as' switcher); otherwise the broadest wins."""
    stamp = (staging.last_sync("dispatch_scope") or {}).get("finished_at") or ""
    if _CACHE.get("__stamp__") != stamp:
        _CACHE.clear()
        _CACHE["__stamp__"] = stamp
    key = (username or "", email or "", bool(admin), persona or "")
    if key in _CACHE:
        return _CACHE[key]

    jcs = _msl.jc_window()
    jc_labels = [{"label": f"JC{j.get('jc')}", "from": str(j.get("from") or ""),
                  "to": str(j.get("to") or "")} for j in jcs]

    grants = []
    if not admin:
        grants = staging.read_user_scope(email=email or None, username=username or None) \
            if (email or username) else []
        if not (persona and any(g["persona"] == persona for g in grants)):
            persona = _pick_persona(grants)

    base = {"persona": persona or ("Admin" if admin else None),
            "jcs": jc_labels, "last_sync": staging.last_sync("dispatch_scope")}

    if admin:
        # full-cube aggregates are the heaviest view — the worker precomputes
        # them after each sync (compute_dashboard_admin); recompute + store on
        # a stale/missing snapshot so the next load is instant either way.
        comp = staging.read_computed("dashboard_admin")
        if comp and (not stamp or (comp.get("last_sync") or {}).get("finished_at") == stamp):
            payload = comp
        else:
            payload = {**base, "scope": _scope_summary("Admin", "", []),
                       **_assemble(staging.dashboard_datasets({}), len(jcs))}
            staging.save_computed("dashboard_admin", payload)
        _CACHE[key] = payload
        return payload
    if not persona:
        return {**base, "scope": [], "kpis": None, "cube": [],
                "top_items": [], "top_customers": []}

    stype, mine, flt = _scope_flt(persona, grants)
    data = _assemble(staging.dashboard_datasets(flt), len(jcs)) if flt else _empty_datasets()
    payload = {**base, "scope": _scope_summary(persona, stype, mine),
               "user_name": mine[0].get("user_name") if mine else None,
               **data}
    _CACHE[key] = payload
    return payload


__all__ = [n for n, v in list(globals().items())
           if callable(v) and getattr(v, "__module__", None) == __name__ and not n.startswith("__")]
