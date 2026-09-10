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

from datetime import date, datetime, timedelta

from ..integration import jc_calendar as _jc
from ..integration import msl as _msl
from ..integration import planning_filter as _pf
from ..integration import staging
from . import item_activity as _activity
from ..integration.planning_filter import _proj_flag   # the plan's ±20% band

# bump when the payload shape changes so stale precomputed admin payloads
# (computed_plan.dashboard_admin) are rebuilt instead of served
_PAYLOAD_V = 32

# broadest scope first — a user holding several personas gets the widest view
_PERSONA_PRIORITY = ["Division Head", "Business Head", "Technical Head",
                     "Technical Manager", "Regional Manager", "Branch Manager",
                     "Sales Executive", "Technical Executive"]

# The accuracy headline scores the LAST N COMPLETED cycles rather than the whole
# accounting year. A projection is judged by how it landed recently — a JC1 miss
# six cycles ago says nothing about how the team is projecting now, and averaging
# it in flattens exactly the movement the card exists to show. The per-JC trend
# beside it still runs the full year, so the history is not lost.
_ACC_JCS = 3
# A cycle is "ending" inside its final week, at which point the plan that matters
# is the next one rather than the one nearly spent.
_LAST_WEEK_DAYS = 7
_CUBE_MAX_COLLECTORS = 12   # cube buckets beyond these become "Other" (keeps the
_CUBE_MAX_SEGMENTS = 10     # client-side cross-filter payload small)


def ctx_acc_year() -> str:
    return str((staging.read_context() or {}).get("acc_year") or "")


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


# shown under the persona chip, so nobody wonders where the traded volume went
_ACTIVITY_NOTE = "Performance Chemicals, made or repacked here — other divisions and traded items excluded"


def _scope_summary(persona: str, stype: str, mine: list[dict]) -> list[str]:
    """The scope lines shown under the persona chip, plus the standing note that
    the page counts only what we make or repack."""
    return _scope_lines(persona, stype, mine) + [_ACTIVITY_NOTE]


def _scope_lines(persona: str, stype: str, mine: list[dict]) -> list[str]:
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


# A quote older than this has stopped being pipeline. 63% of the open book's
# quantity is over a year old (1,284 quotes, 2.86M KG), and CRM's own
# quotation_valid_upto is NULL on 96% of open quotes, so age is the only usable
# signal. Reported separately rather than dropped, exactly like stale SOC.
_QUOTE_LIVE_MONTHS = 12
_COMMERCIAL_CAP = 3000
# A job cycle is 4 weeks and the year holds 13 of them, so the annual budget
# divided by 13 is the per-cycle share a projection should be tracking. It is a
# flat pro-rata: real demand is seasonal, so one cycle can sit either side of it
# legitimately. It is reliable for "nothing at all versus something", which is
# where the projection gap actually lives.
_JC_PER_YEAR = 13
# items listed inside one gap bucket's drill-down (the biggest contributors)
_PART_ROW_CAP = 500


def _commercial_block(flt: dict, flt_c: dict, acc_year: str,
                     gap_parts: list[dict] | None = None) -> dict | None:
    """SOC / open quote / annual potential / annual budget over one scope.

    The four measures come from three tables that share a customer x item grain,
    so they are joined on the item name (normalised) and rolled up three ways.
    SOC is the LIVE committed balance — the same stale rule the order-book pages
    use, so this table and My Supply Position agree.

    ``gap_parts`` is the projection card's gap decomposition. When given, each
    part's item-level rows are replaced by customer x item rows carrying these
    same commercial figures — done here rather than in ``_projection_block``
    because this is where the order book, the quote book and the annual plan
    have already been read, and reading them twice is the expensive part.
    """
    from .competition import STALE_DAYS
    if flt is None:
        return None
    cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    quote_cut = (date.today() - timedelta(days=30 * _QUOTE_LIVE_MONTHS)).isoformat()
    # the SAME cycle the projection card scores, so the two cards agree on which
    # plan is being talked about
    fw = _forward_scope(_msl.jc_window())
    scored_jc = int((fw or {}).get("scored", {}).get("jc") or 0)
    scored_fy = str((fw or {}).get("scored", {}).get("fy") or acc_year)
    scored_label = str((fw or {}).get("scored", {}).get("label") or "")

    # One row per item x customer — the grain all three sources share, so the
    # item and the customer are on the same line rather than in two tables.
    agg: dict = {}

    def add(item_name, cust_name, seg, cust_id, item_key, field, qty):
        if not item_key:
            return
        k = (item_key, cust_id if cust_id is not None else cust_name)
        g = agg.get(k)
        if g is None:
            g = agg[k] = {"key": f"{item_key}|{cust_id if cust_id is not None else cust_name}",
                          "item": item_name, "customer": cust_name or "—", "seg": seg or "",
                          "soc": 0.0, "quote": 0.0, "quote_stale": 0.0,
                          "potential": 0.0, "budget": 0.0, "projection": 0.0,
                          "quotes": set(), "_item": item_key, "_cust": cust_id}
        g[field] += qty
        if not g["seg"] and seg:
            g["seg"] = seg

    # ── SOC: live committed balance ─────────────────────────────────────────
    for r in staging.read_order_commit(flt_c or {}):
        due = r.get("resched_date") or r.get("sched_date")
        if due and str(due)[:10] < cutoff:
            continue                       # stale paperwork, not a live claim
        nm = str(r.get("item_name") or "").strip()
        key = _norm(nm)
        if not key:
            continue
        seg = r.get("segment3") or r.get("segment2") or ""
        add(nm, str(r.get("customer_name") or "—"), seg,
            r.get("customer_id"), key, "soc", float(r.get("balance") or 0))

    # ── open quotes ─────────────────────────────────────────────────────────
    for r in staging.read_open_quotes(flt):
        nm = str(r.get("item_name") or "").strip()
        key = _norm(nm)
        if not key:
            continue
        seg = r.get("segment3") or r.get("segment2") or ""
        live = not r.get("quote_date") or str(r["quote_date"])[:10] >= quote_cut
        add(nm, str(r.get("customer_name") or "—"), seg,
            r.get("customer_id"), key, "quote" if live else "quote_stale",
            float(r.get("qty") or 0))
        if live:
            cid = r.get("customer_id")
            g = agg.get((key, cid if cid is not None else str(r.get("customer_name") or "—")))
            if g is not None:
                g["quotes"].add(r.get("quote_id"))

    # ── the cycle projection, at the same customer x item grain ─────────────
    if scored_jc:
        for r in staging.read_projection_customer(flt, scored_fy, scored_jc):
            nm = str(r.get("item_name") or "").strip()
            key = _norm(nm)
            if not key:
                continue
            add(nm, str(r.get("customer_name") or "—"),
                r.get("segment3") or r.get("segment2") or "",
                r.get("customer_id"), key, "projection", float(r.get("current_q") or 0))

    # ── annual potential + budget ───────────────────────────────────────────
    for r in staging.read_annual_plan(flt, acc_year):
        nm = str(r.get("item_name") or "").strip()
        key = _norm(nm)
        if not key:
            continue
        seg = r.get("segment3") or r.get("segment2") or ""
        cust = r.get("customer_id")
        cname = str(r.get("customer_name") or "—")
        add(nm, cname, seg, cust, key, "potential", float(r.get("potential_qty") or 0))
        add(nm, cname, seg, cust, key, "budget", float(r.get("budget_qty") or 0))

    # normalise the gap parts to row grain FIRST: even with no annual plan,
    # quote or order, the dispatch side can still produce lines, and a part must
    # never be left carrying the item-level shape
    if gap_parts:
        _fill_gap_rows(gap_parts, agg, flt, fw)

    if not agg:
        return None

    rows = []
    for g in agg.values():
        bud_cycle = g["budget"] / _JC_PER_YEAR
        rows.append({
            "key": g["key"], "item": g["item"], "customer": g["customer"], "seg": g["seg"],
            "soc": round(g["soc"], 1), "quote": round(g["quote"], 1),
            "quote_stale": round(g["quote_stale"], 1),
            "potential": round(g["potential"], 1), "budget": round(g["budget"], 1),
            "budget_cycle": round(bud_cycle, 1),
            "projection": round(g["projection"], 1),
            # what the rep planned for this cycle against their own annual budget
            "variance": round(g["projection"] - bud_cycle, 1),
            "quotes": len(g["quotes"]),
        })
    # budget is the planning anchor, so it ranks; the live figures break ties
    rows.sort(key=lambda r: (-(r["budget"] or 0), -(r["soc"] or 0), -(r["quote"] or 0),
                             -(r["potential"] or 0)))

    tot = {f: round(sum(r[f] for r in rows), 1)
           for f in ("soc", "quote", "quote_stale", "potential", "budget",
                     "budget_cycle", "projection", "variance")}
    tot["rows"] = len(rows)
    tot["budgeted_not_projected"] = sum(1 for r in rows
                                        if r["budget"] > 0 and r["projection"] <= 0)
    tot["budgeted_not_projected_kg"] = round(
        sum(r["budget_cycle"] for r in rows if r["budget"] > 0 and r["projection"] <= 0), 1)
    tot["items"] = len({g["_item"] for g in agg.values()})
    tot["customers"] = len({g["_cust"] for g in agg.values() if g["_cust"] is not None})
    tot["quotes"] = len({q for g in agg.values() for q in g["quotes"]})
    return {
        "acc_year": acc_year,
        "quote_months": _QUOTE_LIVE_MONTHS,
        "stale_days": STALE_DAYS,
        "jc_per_year": _JC_PER_YEAR,
        "cycle_label": scored_label,
        "totals": tot,
        "count": len(rows),
        "rows": rows[:_COMMERCIAL_CAP],
    }


# The dashboard reports two DIFFERENT product universes on purpose, and users
# must not read the two counts as a discrepancy:
#
#   dispatch scope   - Performance Chemicals AND made or repacked here. What the
#                      dispatch, projection and RM cards measure.
#   commercial scope - the whole Performance Chemicals range, including products
#                      the division distributes rather than makes. What a budget
#                      and a quotation actually cover.
_SCOPE_NOTE = ("Budget and quotation cover the whole Performance Chemicals range, "
               "including distributed products that dispatch calculations exclude.")


def _scopes(kpis: dict | None, commercial: dict | None) -> dict:
    return {
        "dispatch_items": int((kpis or {}).get("items") or 0),
        "commercial_items": int(((commercial or {}).get("totals") or {}).get("items") or 0),
        "note": _SCOPE_NOTE,
    }


def _fill_gap_rows(gap_parts: list[dict], agg: dict, flt: dict, fw: dict | None) -> None:
    """Replace each gap bucket's item rows with customer x item rows.

    Dispatch is read separately rather than folded into ``agg``: an item that
    merely shipped would otherwise enter the commercial table and inflate the
    Budget & quotation scope count, which names a different universe on purpose.
    """
    done_idx = [n for n, _j in (fw or {}).get("done", [])]
    if not done_idx:
        return
    n_done = len(done_idx)
    disp: dict = {}
    dnames: dict = {}
    for r in staging.dispatch_by_customer_item(
            flt, done_idx, _activity.allowed_item_codes()):
        k = _norm(r.get("item_name"))
        cid = r.get("customer_id")
        disp.setdefault(k, {})[cid] = float(r.get("qty") or 0) / n_done
        dnames[(k, cid)] = str(r.get("customer_name") or "—")

    by_item: dict = {}
    for (ik, cid), g in agg.items():
        by_item.setdefault(ik, {})[cid] = g

    # every item the buckets cover, from whichever bucket its totals landed in
    items: dict = {}
    for part in gap_parts:
        for ir in (part.get("rows") or []):
            if ir.get("key"):
                items[ir["key"]] = ir

    def bucket_of(proj: float, disp: float, diff: float) -> str:
        if proj > 0 and disp > 0:
            return "over" if diff >= 0 else "under"
        return "new" if proj > 0 else "missing"

    lines: dict = {"over": [], "under": [], "new": [], "missing": []}
    for ik, ir in items.items():
        seg = ir.get("seg") or ""
        seen: dict = {}
        for cid, g in (by_item.get(ik) or {}).items():
            seen[cid] = {"customer": g["customer"], "soc": g["soc"],
                         "quote": g["quote"], "potential": g["potential"],
                         "budget": g["budget"], "projection": g["projection"],
                         "dispatch": 0.0}
        for cid, q in (disp.get(ik) or {}).items():
            e = seen.setdefault(cid, {"customer": dnames.get((ik, cid), "—"),
                                      "soc": 0.0, "quote": 0.0, "potential": 0.0,
                                      "budget": 0.0, "projection": 0.0, "dispatch": 0.0})
            e["dispatch"] = q
        for cid, e in seen.items():
            # classify on the SAME rounded figures the table shows, or a line
            # carrying 0.04 KG of projection is filed as "projected below recent
            # sales" while displaying a projection of 0.0
            pv = round(e["projection"], 1)
            dv = round(e["dispatch"], 1)
            # only lines that actually MOVE the gap belong here. A customer with
            # an order or a budget but the same projection as dispatch
            # contributes nothing to any bucket and is just noise.
            diff = round(pv - dv, 1)
            if diff == 0:
                continue
            lines[bucket_of(pv, dv, diff)].append({
                "item": ir.get("item"), "customer": e["customer"], "seg": seg,
                "soc": round(e["soc"], 1), "quote": round(e["quote"], 1),
                "potential": round(e["potential"], 1), "budget": round(e["budget"], 1),
                "projection": pv, "dispatch": dv, "diff": diff,
            })

    for part in gap_parts:
        out = lines.get(part["key"], [])
        out.sort(key=lambda r: -abs(r["diff"]))
        # the part now DESCRIBES its own lines rather than the items above them
        part["kg"] = round(sum(r["diff"] for r in out), 1)
        part["items"] = len({r["item"] for r in out})
        part["row_items"] = part["items"]
        part["row_count"] = len(out)
        part["rows_kg"] = part["kg"]
        part["rows"] = out[:_PART_ROW_CAP]


def _empty_datasets() -> dict:
    return {"kpis": {"qty": 0, "value": 0, "customers": 0, "items": 0,
                     "last_jc_qty": 0, "prev_jc_qty": 0},
            "cube": [], "top_items": [], "top_customers": [], "projection": None}


def _norm(s) -> str:
    return str(s or "").strip().upper()


def _proj_map(mine: list[dict], admin: bool, acc_year: str, jc: int,
              stype: str | None = None, flt: dict | None = None):
    """(projection map, basis): norm item name -> current/next1/next2 KG.

    ``basis`` says where the numbers came from, because the three sources are
    scoped differently:

      "customer"  - any scope keyed to collectors, circles or customers. The
                    projection is recorded per item x collector, which is WIDER
                    than a market circle: reading the collector rows charged one
                    Sales Executive with 30,110 KG against the 15,730 KG in his
                    own circle. Reading the customer grain also keys collectors
                    by ID rather than by NAME, which is what left one Branch
                    Manager's buckets 50 KG short of his own gap.
      "collector" - kept for a scope with collector names but no usable filter.
      "item"      - everyone else, from the item-level plan table.

    Traded items are dropped here as well as on the dispatch side — otherwise a
    projected-but-not-selling solvent would still surface as a 'new' item and
    the coverage percentages would be measured against a universe the rest of
    the page no longer counts."""
    if not admin and stype in ("market_circle", "customer", "collector") and flt:
        proj: dict = {}
        for r in staging.read_projection_customer(flt, acc_year, int(jc)):
            k = _norm(r.get("item_name"))
            if not k or (keep_map := _activity.activity_map()) and \
                    _pf._squash(r.get("item_name")) not in keep_map:
                continue
            e = proj.setdefault(k, {"proj": 0.0, "next1": 0.0, "next2": 0.0,
                                    "name": str(r.get("item_name")).strip(),
                                    "s2": r.get("segment2"), "s3": r.get("segment3")})
            e["proj"] += float(r.get("current_q") or 0)
            e["next1"] += float(r.get("next1_q") or 0)
            e["next2"] += float(r.get("next2_q") or 0)
        return proj, "customer"

    coll_names = {g.get("collector_name") for g in mine if g.get("collector_name")}
    use_rows = bool(coll_names) and not admin
    if use_rows:
        wanted = {_norm(c) for c in coll_names}
        rows = [r for r in staging.read_projection_rows(acc_year, jc)
                if _norm(r.get("Collector")) in wanted]
    else:
        rows = staging.read_projection(acc_year, jc, approved=True)
    proj: dict = {}
    keep = _activity.activity_map()
    for r in rows:
        k = _norm(r.get("ItemName"))
        if not k or (keep and _pf._squash(r.get("ItemName")) not in keep):
            continue
        p = proj.setdefault(k, {"proj": 0.0, "next1": 0.0, "next2": 0.0,
                                "name": str(r.get("ItemName")).strip(),
                                "s2": r.get("Segment2"), "s3": r.get("Segment3")})
        p["proj"] += float(r.get("CurrentQ") or 0)
        p["next1"] += float(r.get("Next1Q") or 0)
        p["next2"] += float(r.get("Next2Q") or 0)
    return proj, ("collector" if use_rows else "item")


def _forward_scope(window: list[dict], today: date | None = None) -> dict | None:
    """Which cycle's projection to score, and which cycles to score it against.

    ``jc_window()`` ends at the cycle we are CURRENTLY INSIDE, so its last entry
    is partly dispatched and the completed cycles are the ones before it. Inside
    the final week that current cycle is nearly spent, so the plan worth checking
    is the next one.
    """
    if not window:
        return None
    today = today or date.today()

    def _d(v):
        try:
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    cur = window[-1]
    end = _d(cur.get("to"))
    start = _d(cur.get("from"))
    days_left = (end - today).days if end else None
    in_last_week = bool(end and start and start <= today <= end
                        and days_left < _LAST_WEEK_DAYS)

    scored = cur
    if in_last_week:
        allj = sorted(_jc._all_jcs(), key=lambda j: j["start"])
        i = next((n for n, j in enumerate(allj)
                  if j.get("fy") == cur.get("fy") and j.get("jc") == cur.get("jc")), None)
        if i is not None and i + 1 < len(allj):
            scored = allj[i + 1]
        else:
            in_last_week = False        # no next cycle on the calendar yet

    # the completed cycles: everything in the window that has already ended
    done = [(n, j) for n, j in enumerate(window) if (_d(j.get("to")) or today) < today]
    return {"scored": scored, "current": cur, "in_last_week": in_last_week,
            "days_left": days_left, "done": done[-_ACC_JCS:]}


def _proj_history(basis: str, mine: list[dict], acc_year: str,
                  flt: dict | None) -> list[dict]:
    """Every staged cycle's projection, sliced the SAME way ``_proj_map`` slices
    the current one — otherwise a trend and its own headline would be measured
    over different books."""
    if basis == "customer":
        return staging.read_projection_customer_all(flt or {}, acc_year)
    if basis == "collector":
        wanted = {_norm(c) for c in
                  {g.get("collector_name") for g in mine if g.get("collector_name")}}
        return [r for r in staging.read_projection_rows_all(acc_year)
                if _norm(r.get("collector")) in wanted]
    return staging.read_projection_all(acc_year, approved=True)


def _wmape_acc(pairs) -> float | None:
    """Accuracy % over (projected, actual) pairs — 100 - WMAPE, the same metric
    the Projection-Accuracy page uses (projection_accuracy._metrics). Summing the
    per-item absolute variance (not the netted totals) keeps over- and
    under-projections from cancelling each other out."""
    act = sum(a for _, a in pairs)
    if act <= 0:
        return None
    err = sum(abs(pr - a) for pr, a in pairs)
    return round(max(0.0, 100 - 100 * err / act), 1)


def _weighted_mean(rows) -> float | None:
    """Volume-weighted mean of the per-JC accuracies.

    Aggregate the per-JC figures rather than pooling raw errors across cycles:
    WMAPE is floored at 0 per cycle, so one cycle whose projection overshoots
    badly can push the POOLED ratio below zero and floor the headline at 0%
    even when most cycles scored well — which made the gauge contradict the
    per-JC table right beside it. Weighting by each cycle's actual volume keeps
    big cycles dominant while the headline always sits inside the range the
    table shows."""
    tot = sum(w for a, w in rows if a is not None and w > 0)
    if not tot:
        return None
    return round(sum(a * w for a, w in rows if a is not None and w > 0) / tot, 1)


def _projection_block(sales3: list[dict], item_jc: list[dict], window: list[dict],
                      mine: list[dict], stype: str, admin: bool = False,
                      flt: dict | None = None) -> dict | None:
    """Projection accuracy for the user's scope: the plan-table projection
    (stg_projection CurrentQ for the planning JC — the same slice the RM plan
    build reads) vs the scoped 3-JC AVERAGE sales per item, flagged with the
    plan's own ±20% band (_proj_flag: over / under / ontrack / new). Items with
    sales but NO projection ('none') are the submission gaps to highlight.

    Also returns the per-JC trend (projected vs actual + accuracy for every
    completed JC of the accounting year), the item-group roll-up and the full
    missing-projection list.

    When the scope names specific collectors, projections come from
    stg_projection_rows summed over those collectors (same CRM projection data,
    per-collector slice) so the comparison is apples-to-apples."""
    ctx = staging.read_context() or {}
    acc_year, jc = ctx.get("acc_year"), ctx.get("plan_jc")
    if not acc_year or not jc:
        return None

    proj, basis = _proj_map(mine, admin, acc_year, int(jc), stype, flt)
    use_rows = basis == "collector"

    # scoped items with sales in the last 3 JCs
    items = []
    seen = set()
    for s in sales3:
        k = _norm(s.get("name"))
        if not k:
            continue
        seen.add(k)
        avg3 = float(s.get("qty3") or 0) / 3.0
        pr = proj.get(k) or {}
        p = pr.get("proj", 0.0)
        # 'none' = sells but NO projection submitted at all — the gap to
        # highlight (the plan's _proj_flag would call proj=0 'under')
        flag = "none" if (p <= 0 and avg3 > 0) else _proj_flag(p, avg3)
        items.append({"name": str(s.get("name")).strip(), "code": s.get("code"),
                      "proj": round(p, 1), "avg3": round(avg3, 1), "flag": flag,
                      "next1": pr.get("next1", 0.0), "next2": pr.get("next2", 0.0)})

    # projected-but-not-selling items ('new') — only where the projection side
    # is scoped tightly enough to be meaningful for this persona
    grant_s2 = {g["segment2"] for g in mine if g.get("segment2") and not g.get("segment3")
                and not g.get("segment4")}
    grant_s3 = {g["segment3"] for g in mine if g.get("segment3") and not g.get("segment4")}
    for k, p in proj.items():
        if k in seen or p["proj"] <= 0:
            continue
        # only where the projection slice matches the sales slice exactly:
        # whole company, whole collector(s), or a segment-filtered division —
        # narrower scopes (circle / customer / segment+collector) would pull
        # unrelated projected items in
        # the projection slice must match the sales slice. It now does for a
        # market circle or a customer scope too, since those read their own
        # customers rather than the whole collector.
        ok = admin or basis == "customer" or (use_rows and stype == "collector") or \
            (stype == "segment" and basis == "item"
             and (p.get("s2") in grant_s2 or p.get("s3") in grant_s3))
        if ok:
            items.append({"name": p["name"], "code": None, "proj": round(p["proj"], 1),
                          "avg3": 0.0, "flag": "new",
                          "next1": p.get("next1", 0.0), "next2": p.get("next2", 0.0)})

    total_avg3 = sum(i["avg3"] for i in items) or 1.0
    covered = sum(i["avg3"] for i in items if i["proj"] > 0)
    summary = []
    for flag in ("ontrack", "over", "under", "none", "new"):
        sub = [i for i in items if i["flag"] == flag]
        if sub:
            summary.append({"flag": flag, "items": len(sub),
                            "kg": round(sum(i["avg3"] for i in sub))})
    with_sales = sorted([i for i in items if i["avg3"] > 0], key=lambda i: -i["avg3"])
    # projection pipeline over this scope's item universe: the planning JC and
    # the two after it (stg_projection CurrentQ / Next1Q / Next2Q)
    pipeline = [
        {"key": "current", "label": f"Current · JC{int(jc)}",
         "kg": round(sum(i["proj"] for i in items)),
         "items": sum(1 for i in items if i["proj"] > 0)},
        {"key": "next1", "label": "Next JC",
         "kg": round(sum(i["next1"] for i in items)),
         "items": sum(1 for i in items if i["next1"] > 0)},
        {"key": "next2", "label": "JC after next",
         "kg": round(sum(i["next2"] for i in items)),
         "items": sum(1 for i in items if i["next2"] > 0)},
    ]

    # row-by-row product detail for the pipeline table: top items by projected
    # volume (then by sales) — capped so huge scopes don't bloat the payload
    # Rank by whichever side is bigger — sorting on projected volume alone would
    # push the biggest UNPROJECTED sellers (the ones worth acting on) past the cap.
    pipeline_rows = sorted(
        (i for i in items if i["proj"] or i["next1"] or i["next2"] or i["avg3"]),
        key=lambda i: -max(i["avg3"], i["proj"], i["next1"], i["next2"]))[:400]
    # tag each item with the SAME segment expression the dispatch cube groups by,
    # so drilling into a slice of the segment chart lands on the right items
    cube_seg: dict = {}
    cube_name: dict = {}
    for r in (item_jc or []):
        kk = _norm(r.get("name"))
        if kk and kk not in cube_seg:
            cube_seg[kk] = r.get("segment3") or r.get("segment2") or "—"
            cube_name[kk] = str(r.get("name") or "").strip()
    pipeline_rows = [{"code": i.get("code"), "name": i["name"], "avg3": i["avg3"],
                      "proj": round(i["proj"], 1), "next1": round(i["next1"], 1),
                      "next2": round(i["next2"], 1), "flag": i["flag"],
                      "seg": cube_seg.get(_norm(i["name"]), "—")}
                     for i in pipeline_rows]

    for i in items:   # next1/next2 fed the pipeline only — keep the payload lean
        i.pop("next1", None)
        i.pop("next2", None)

    # ── item groups (Segment 2 / Segment 3): projection vs 3-JC avg sales ──────
    seg_of: dict = {}
    for k, pr in proj.items():
        seg_of[k] = (pr.get("s2") or "Unmapped", pr.get("s3") or "Unmapped")
    for r in (item_jc or []):          # cube segments fill in the non-projected items
        k = _norm(r.get("name"))
        if k and k not in seg_of:
            seg_of[k] = (r.get("segment2") or "Unmapped", r.get("segment3") or "Unmapped")
    groups: dict = {"segment2": {}, "segment3": {}}
    for i in items:
        s2, s3 = seg_of.get(_norm(i["name"]), ("Unmapped", "Unmapped"))
        for level, name in (("segment2", s2), ("segment3", s3)):
            g = groups[level].setdefault(name, {"name": name, "proj": 0.0, "avg3": 0.0,
                                                "items": 0, "missing": 0, "pairs": []})
            g["proj"] += i["proj"]
            g["avg3"] += i["avg3"]
            g["items"] += 1
            g["missing"] += 1 if i["flag"] == "none" else 0
            g["pairs"].append((i["proj"], i["avg3"]))
    by_group = {}
    for level, gm in groups.items():
        out = [{"name": g["name"], "proj": round(g["proj"]), "avg3": round(g["avg3"]),
                "items": g["items"], "missing": g["missing"],
                "covered_kg": round(sum(a for pr, a in g["pairs"] if pr > 0)),
                "uncovered_kg": round(sum(a for pr, a in g["pairs"] if pr <= 0)),
                "accuracy": _wmape_acc(g["pairs"]),
                "accuracy_proj": _wmape_acc([x for x in g["pairs"] if x[0] > 0])}
               for g in gm.values()]
        by_group[level] = sorted(out, key=lambda d: -(d["proj"] + d["avg3"]))[:14]

    # ── per-JC trend: projected vs actual + accuracy, for every completed JC ───
    # Only this accounting year is staged, so the trend covers JC1..current.
    allowed = None if admin else {_norm(i["name"]) for i in items}
    act_by_jc: dict = {}
    for r in (item_jc or []):
        k = _norm(r.get("name"))
        if not k or (allowed is not None and k not in allowed):
            continue
        d = act_by_jc.setdefault(int(r["jc"]), {})
        d[k] = d.get(k, 0.0) + float(r.get("qty") or 0)

    # the history must be sliced the SAME way as the forward figure, or the
    # trend and the gap would be measured over different books
    hist = _proj_history(basis, mine, acc_year, flt)
    proj_by_jc: dict = {}
    # Both sides of the comparison must cover the same universe. The actuals come
    # from the dispatch cube, already filtered to what we make or repack; without
    # the same filter here every projected traded item would arrive with a
    # projection and no dispatch, scoring as a total miss — which floored the
    # Admin accuracy at 0% in every cycle.
    keep = _activity.activity_map()
    for r in hist:
        k = _norm(r.get("item_name"))
        if not k or (allowed is not None and k not in allowed):
            continue
        if keep and _pf._squash(r.get("item_name")) not in keep:
            continue
        d = proj_by_jc.setdefault(int(r["jc"]), {})
        d[k] = d.get(k, 0.0) + float(r.get("current_q") or 0)

    # Every staged JC of the year — including the PLANNING JC, which has
    # projections but no actuals yet (its actual/accuracy come back as null so
    # the qty and item-count charts still show it).
    win = {int(w.get("jc") or 0): (i, w) for i, w in enumerate(window or [])
           if str(w.get("fy")) == str(acc_year)}
    jc_trend, acc_rows, acc_rows_p, done_jcs = [], [], [], []
    for wjc in sorted(set(proj_by_jc) | set(win)):
        idx, w = win.get(wjc, (None, {}))
        pj = proj_by_jc.get(wjc, {})
        ac = act_by_jc.get(idx, {}) if idx is not None else {}
        done = idx is not None          # a completed JC we can score
        if not pj and not ac:
            continue
        pairs = [(pj.get(k, 0.0), ac.get(k, 0.0)) for k in set(pj) | set(ac)]
        pairs_p = [x for x in pairs if x[0] > 0]
        act_tot = sum(a for _, a in pairs)
        act_tot_p = sum(a for _, a in pairs_p)
        if done:
            acc_rows.append((_wmape_acc(pairs), act_tot))
            acc_rows_p.append((_wmape_acc(pairs_p), act_tot_p))
            done_jcs.append(wjc)          # ascending, so the last N are the recent ones
        jc_trend.append({
            "label": f"JC{wjc}", "jc": wjc, "done": done,
            "from": str(w.get("from") or ""), "to": str(w.get("to") or ""),
            "proj": round(sum(pj.values())),
            "actual": round(sum(ac.values())) if done else None,
            "items_projected": sum(1 for v in pj.values() if v > 0),
            "items_sold": sum(1 for v in ac.values() if v > 0) if done else None,
            "accuracy": _wmape_acc(pairs) if done else None,
            "accuracy_proj": _wmape_acc(pairs_p) if done else None,
            "coverage_pct": (round(100 * sum(a for _, a in pairs_p) / act_tot, 1)
                             if done and act_tot > 0 else None),
        })

    # ── the forward check: the plan that matters now vs how we have been
    # selling. Not a hindsight score — it asks whether the projection in front
    # of us is plausible against the last three COMPLETED cycles.
    forward = None
    fw = _forward_scope(window)
    if fw:
        fproj, _b = _proj_map(mine, admin, str(fw["scored"].get("fy") or acc_year),
                              int(fw["scored"].get("jc") or 0), stype, flt)
        rate: dict = {}
        for n, _j in fw["done"]:
            for k, v in (act_by_jc.get(n) or {}).items():
                rate[k] = rate.get(k, 0.0) + v
        n_done = len(fw["done"]) or 1
        rate = {k: v / n_done for k, v in rate.items()}
        if allowed is not None:
            fproj = {k: v for k, v in fproj.items() if k in allowed}
            rate = {k: v for k, v in rate.items() if k in allowed}
        pairs_f = [(fproj.get(k, {}).get("proj", 0.0), rate.get(k, 0.0))
                   for k in set(fproj) | set(rate)]
        pairs_fp = [x for x in pairs_f if x[0] > 0]

        # Where the gap comes from. Every item falls into exactly one bucket and
        # the four add up to the gap itself, so the decomposition can be checked
        # rather than believed: a plan that is merely cautious looks nothing like
        # one that omits half the book, and the card could not tell them apart.
        parts = {"over": [0.0, 0], "under": [0.0, 0], "new": [0.0, 0], "missing": [0.0, 0]}
        part_rows: dict = {"over": [], "under": [], "new": [], "missing": []}
        for k in set(fproj) | set(rate):
            pv = fproj.get(k, {}).get("proj", 0.0)
            av = rate.get(k, 0.0)
            if pv <= 0 and av <= 0:
                continue
            diff = pv - av
            if pv > 0 and av > 0:
                b = "over" if diff >= 0 else "under"
            elif pv > 0:
                b = "new"
            else:
                b = "missing"
            parts[b][0] += diff
            parts[b][1] += 1
            pr = fproj.get(k) or {}
            part_rows[b].append({
                "key": k,
                "item": pr.get("name") or cube_name.get(k) or k,
                "seg": cube_seg.get(k) or pr.get("s3") or pr.get("s2") or "—",
                "projection": round(pv, 1), "dispatch": round(av, 1),
                "diff": round(diff, 1),
            })
        for b in part_rows:
            part_rows[b].sort(key=lambda r: -abs(r["diff"]))
        # The comparison is TOTAL to TOTAL, as the KPI definitions say: all of the
        # upcoming projection against all of the recent dispatch. Restricting the
        # dispatch side to items that happen to carry a projection would flatter
        # the ratio by hiding everything selling with no plan behind it.
        projection = sum(p for p, _a in pairs_f)
        avg = sum(a for _p, a in pairs_f)
        gap = projection - avg
        forward = {
            "jc": int(fw["scored"].get("jc") or 0),
            "label": str(fw["scored"].get("label") or ""),
            "basis": "next" if fw["in_last_week"] else "current",
            "current_label": str(fw["current"].get("label") or ""),
            "in_last_week": fw["in_last_week"],
            "days_left": fw["days_left"],
            "dispatch_jcs": [str(j.get("label") or "") for _n, j in fw["done"]],
            "n_cycles": len(fw["done"]),
            # the five KPIs
            "projection_kg": round(projection, 1),
            "dispatch_avg_kg": round(avg, 1),
            "ratio_pct": (round(100.0 * projection / avg, 2) if avg > 0 else None),
            "uplift_pct": (round(100.0 * gap / avg, 2) if avg > 0 else None),
            "gap_kg": round(gap, 1),
            # context for the line under the table
            "dispatch_total_kg": round(avg * (len(fw["done"]) or 1), 1),
            "items_projected": sum(1 for p, _a in pairs_f if p > 0),
            "items_sold": sum(1 for _p, a in pairs_f if a > 0),
            # kept so the per-cycle history can still be reconciled
            "accuracy": _wmape_acc(pairs_fp),
            "accuracy_all": _wmape_acc(pairs_f),
            "projected_items_dispatch_avg_kg": round(sum(a for _p, a in pairs_fp), 1),
            "parts": [
                {"key": key, "label": label,
                 "kg": round(parts[key][0], 1), "items": parts[key][1],
                 "rows": part_rows[key][:_PART_ROW_CAP]}
                for key, label in (
                    ("under", "Projected below recent sales"),
                    ("missing", "Selling, not projected at all"),
                    ("over", "Projected above recent sales"),
                    ("new", "Projected, no recent sales"),
                )
            ],
        }

    # every scoped item grouped by its status, so the status chart can drill
    # into any slice (biggest seller first; projected-but-not-selling items have
    # no sales to rank by, so they fall back to projected volume)
    by_flag: dict = {}
    for i in sorted(items, key=lambda d: (-(d["avg3"] or 0), -(d["proj"] or 0))):
        by_flag.setdefault(i["flag"], []).append(
            {"name": i["name"], "code": i["code"], "avg3": i["avg3"], "proj": i["proj"]})
    items_by_flag = {k: v[:200] for k, v in by_flag.items()}

    # the card lists these item by item, so each carries the segment the rest of
    # the page groups by
    missing_all = [{"name": i["name"], "code": i["code"], "avg3": i["avg3"],
                    "seg": cube_seg.get(_norm(i["name"]), "—")}
                   for i in with_sales if i["flag"] == "none"][:500]
    return {
        "acc_year": acc_year, "jc": int(jc),
        "basis": basis,
        "coverage_pct": round(covered / total_avg3 * 100, 1),
        "summary": summary,
        "items_by_flag": items_by_flag,
        "pipeline": pipeline,
        "pipeline_rows": pipeline_rows,
        "compare": with_sales[:12],
        "jc_trend": jc_trend,
        "forward": forward,
        "overall_accuracy": _weighted_mean(acc_rows[-_ACC_JCS:]),
        "overall_accuracy_proj": _weighted_mean(acc_rows_p[-_ACC_JCS:]),
        # which cycles that headline actually covers, so the card can name them
        "accuracy_jcs": [f"JC{j}" for j in done_jcs[-_ACC_JCS:]],
        "by_group": by_group,
        "items_projected": pipeline[0]["items"],
        "items_selling": sum(1 for i in items if i["avg3"] > 0),
        "covered_kg": round(covered),
        "uncovered_kg": round(sum(i["avg3"] for i in items if i["proj"] <= 0)),
        "missing": missing_all[:15],
        "missing_all": missing_all,
        "missing_total": sum(1 for i in items if i["flag"] == "none"),
        "missing_kg": round(sum(i["avg3"] for i in items if i["flag"] == "none")),
    }


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


def item_detail(username: str | None = None, email: str | None = None,
                admin: bool = False, persona: str | None = None,
                item: str = "", code: str | None = None) -> dict:
    """One item's JC-by-JC dispatch within the caller's scope + its projection
    (current/next/next-next) — the click-to-drill popup on My Dashboard."""
    jcs = _msl.jc_window()
    jc_labels = [{"label": f"JC{j.get('jc')}", "from": str(j.get("from") or ""),
                  "to": str(j.get("to") or "")} for j in jcs]

    grants = []
    if not admin:
        grants = staging.read_user_scope(email=email or None, username=username or None) \
            if (email or username) else []
        if not (persona and any(g["persona"] == persona for g in grants)):
            persona = _pick_persona(grants)

    if admin:
        mine, flt = [], {}
    elif persona:
        stype, mine, flt = _scope_flt(persona, grants)
    else:
        mine, flt = [], None

    qty = [0.0] * len(jcs)
    if flt is not None and (item or code):
        for r in staging.dashboard_item_series(flt, item_code=code or None,
                                               item_name=item or None):
            i = int(r["jc"])
            if 0 <= i < len(jcs):
                qty[i] = round(float(r["qty"] or 0), 1)
    avg3 = round(sum(qty[-3:]) / 3.0, 1) if len(qty) >= 3 else 0.0

    proj_v = n1 = n2 = 0.0
    basis, plan_jc, acc_year = "item", None, None
    ctx = staging.read_context() or {}
    if ctx.get("acc_year") and ctx.get("plan_jc"):
        acc_year, plan_jc = ctx["acc_year"], int(ctx["plan_jc"])
        pm, basis = _proj_map(mine, admin, acc_year, plan_jc, stype, flt)
        e = pm.get(_norm(item)) or {}
        proj_v = round(e.get("proj", 0.0), 1)
        n1 = round(e.get("next1", 0.0), 1)
        n2 = round(e.get("next2", 0.0), 1)


    flag = "none" if (proj_v <= 0 and avg3 > 0) else _proj_flag(proj_v, avg3)
    return {"item": item, "code": code, "jcs": jc_labels, "qty": qty,
            "avg3": avg3, "proj": proj_v, "next1": n1, "next2": n2,
            "flag": flag, "basis": basis, "plan_jc": plan_jc, "acc_year": acc_year}


# How many completed cycles the Projection-by-JC download details. Four lands on
# JC2..JC5 today: JC6 is still running and JC1 carries almost no projection.
JC_DETAIL_CYCLES = 4
_JC_DETAIL_ITEM_CAP = 2000


def jc_item_detail(username: str | None = None, email: str | None = None,
                   admin: bool = False, persona: str | None = None,
                   cycles: int = JC_DETAIL_CYCLES) -> dict | None:
    """Per item x completed cycle: projected, actual and accuracy.

    Built on demand for the Projection-by-JC download — the page payload only
    carries per-cycle TOTALS and a per-item 3-cycle average, and shipping this to
    every load would cost every persona for something only one of them opens.

    Dispatch to ``staging.EXCLUDED_COLLECTORS`` is left out here as it is
    everywhere else, so an item whose only movement was an inter-group transfer
    does not appear on the sheet at all.
    """
    grants = []
    if not admin:
        grants = staging.read_user_scope(email=email or None, username=username or None) \
            if (email or username) else []
        if not (persona and any(g["persona"] == persona for g in grants)):
            persona = _pick_persona(grants)
        if not persona:
            return None
        stype, mine, flt = _scope_flt(persona, grants)
        if flt is None:
            return None
    else:
        stype, mine, flt = "", [], {}

    ctx = staging.read_context() or {}
    acc_year = ctx.get("acc_year")
    if not acc_year:
        return None
    window = _msl.jc_window()
    today = date.today()

    def _d(v):
        try:
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    # completed cycles of THIS accounting year, most recent `cycles` of them
    done = [(i, w) for i, w in enumerate(window)
            if str(w.get("fy")) == str(acc_year) and (_d(w.get("to")) or today) < today]
    done = done[-int(cycles):]
    if not done:
        return None
    idx_label = {i: str(w.get("label") or f"JC{w.get('jc')}") for i, w in done}
    labels = [idx_label[i] for i, _w in done]
    jc_of_idx = {i: int(w.get("jc") or 0) for i, w in done}

    ds = staging.dashboard_datasets(flt, jc_from=max(0, len(window) - 3),
                                    item_codes=_activity.allowed_item_codes())
    # actual per item x cycle, and the names/segments to label them with
    act: dict = {}
    name_of, seg_of, code_of = {}, {}, {}
    for r in (ds["item_jc"] or []):
        i = int(r["jc"])
        if i not in jc_of_idx:
            continue
        k = _norm(r.get("name"))
        if not k:
            continue
        name_of.setdefault(k, str(r.get("name")).strip())
        seg_of.setdefault(k, r.get("segment3") or r.get("segment2") or "")
        act.setdefault(k, {})[i] = act.get(k, {}).get(i, 0.0) + float(r.get("qty") or 0)
    for r in (ds["sales3"] or []):
        k = _norm(r.get("name"))
        if k and r.get("code"):
            code_of.setdefault(k, r["code"])

    _pm, basis = _proj_map(mine, admin, acc_year, int(ctx.get("plan_jc") or 0), stype, flt)
    keep = _activity.activity_map()
    want_jc = {jc_of_idx[i]: i for i in jc_of_idx}
    proj: dict = {}
    for r in _proj_history(basis, mine, acc_year, flt):
        jc = int(r.get("jc") or 0)
        if jc not in want_jc:
            continue
        nm = r.get("item_name")
        k = _norm(nm)
        if not k or (keep and _pf._squash(nm) not in keep):
            continue
        name_of.setdefault(k, str(nm).strip())
        seg_of.setdefault(k, r.get("segment3") or r.get("segment2") or "")
        i = want_jc[jc]
        proj.setdefault(k, {})[i] = proj.get(k, {}).get(i, 0.0) + float(r.get("current_q") or 0)

    label = {"manufacturing": "Manufacturing", "repack_relabel": "Repack/Relabel"}
    rows = []
    for k in set(act) | set(proj):
        per = {}
        for i, _w in done:
            per[idx_label[i]] = {
                "proj": round((proj.get(k) or {}).get(i, 0.0), 1),
                "act": round((act.get(k) or {}).get(i, 0.0), 1),
            }
        tp = round(sum(v["proj"] for v in per.values()), 1)
        ta = round(sum(v["act"] for v in per.values()), 1)
        if not tp and not ta:
            continue
        rows.append({"key": k, "item": name_of.get(k, k), "code": code_of.get(k, ""),
                     "seg": seg_of.get(k, ""),
                     # activity_map is keyed by the SQUASHED name, not _norm
                     "activity": label.get(keep.get(_pf._squash(name_of.get(k, k))), ""),
                     "per": per, "total_proj": tp, "total_act": ta})
    rows.sort(key=lambda r: -r["total_act"])
    return {"cycles": labels, "acc_year": acc_year, "persona": persona or "Admin",
            "count": len(rows), "rows": rows[:_JC_DETAIL_ITEM_CAP]}


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

    base = {"v": _PAYLOAD_V, "persona": persona or ("Admin" if admin else None),
            "jcs": jc_labels, "last_sync": staging.last_sync("dispatch_scope")}
    jc_from = max(0, len(jcs) - 3)   # the projection-accuracy 3-JC sales window

    if admin:
        # full-cube aggregates are the heaviest view — the worker precomputes
        # them after each sync (compute_dashboard_admin); recompute + store on
        # a stale/missing/old-shape snapshot so the next load is instant either way.
        comp = staging.read_computed("dashboard_admin")
        if comp and comp.get("v") == _PAYLOAD_V and \
                (not stamp or (comp.get("last_sync") or {}).get("finished_at") == stamp):
            payload = comp
        else:
            ds = staging.dashboard_datasets({}, jc_from=jc_from,
                                            item_codes=_activity.allowed_item_codes())
            proj = _projection_block(ds["sales3"], ds["item_jc"], jcs,
                                     [], "", admin=True, flt={})
            payload = {**base, "scope": _scope_summary("Admin", "", []),
                       **_assemble(ds, len(jcs)),
                       "projection": proj,
                       "commercial": _commercial_block(
                           {}, {}, ctx_acc_year(),
                           gap_parts=((proj or {}).get("forward") or {}).get("parts"))}
            payload["scopes"] = _scopes(payload.get("kpis"), payload.get("commercial"))
            staging.save_computed("dashboard_admin", payload)
        _CACHE[key] = payload
        return payload
    if not persona:
        return {**base, "scope": [], "kpis": None, "cube": [],
                "top_items": [], "top_customers": [], "projection": None}

    stype, mine, flt = _scope_flt(persona, grants)
    if flt:
        ds = staging.dashboard_datasets(flt, jc_from=jc_from,
                                        item_codes=_activity.allowed_item_codes())
        # imported lazily: commit.py imports from this module
        from .commit import _commit_flt   # order-book scope keys collectors by NAME
        _stc, _mnc, flt_c = _commit_flt(persona, grants)
        proj = _projection_block(ds["sales3"], ds["item_jc"], jcs, mine, stype, flt=flt)
        data = {**_assemble(ds, len(jcs)), "projection": proj,
                "commercial": _commercial_block(
                    flt, flt_c, ctx_acc_year(),
                    gap_parts=((proj or {}).get("forward") or {}).get("parts"))}
    else:
        data = _empty_datasets()
    payload = {**base, "scope": _scope_summary(persona, stype, mine),
               "user_name": mine[0].get("user_name") if mine else None,
               **data}
    payload["scopes"] = _scopes(payload.get("kpis"), payload.get("commercial"))
    _CACHE[key] = payload
    return payload


__all__ = [n for n, v in list(globals().items())
           if callable(v) and getattr(v, "__module__", None) == __name__ and not n.startswith("__")]
