"""Production Job Scheduling engine.

For a confirmed JC plan (plan_id in JC_PLAN), lay out production jobs on a
calendar. Each plan FG quantity is split into three SOC scenarios, ranked by a
six-level priority (SOC class x RM availability), sized into batches via the
vessel_product_mapping (batch size + cycle time), and placed on the equipment
timeline from a start date driven by the priority (JC start / SOC schedule date /
RM lead time).

SOC scenarios (per item):
  Pending SOC : open SOC with schedule date on/before the JC start date.
  Future SOC  : open SOC with schedule date after the JC start date.
  No SOC      : plan qty not covered by any SOC (locked projection balance).

Priority:
  1 Pending SOC + RM available          4 Future SOC + RM not available
  2 Future SOC + RM available           5 No SOC + RM available
  3 Pending SOC + RM not available      6 No SOC + RM not available
"""
from __future__ import annotations

import math
from bisect import insort
from collections import Counter
from datetime import date, timedelta

from . import planning_filter as pf

PRIORITY = {("pending", True): 1, ("future", True): 2, ("pending", False): 3,
            ("future", False): 4, ("no_soc", True): 5, ("no_soc", False): 6}
SCENARIO = {"pending": "Pending SOC", "future": "Future SOC", "no_soc": "No SOC"}


def _hours(v) -> float:
    """Parse a cycle-time cell into hours: 'HH:MM:SS', 'N day(s), HH:MM:SS',
    or a decimal number of hours."""
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    days = 0.0
    if "day" in s.lower():
        parts = s.split(",", 1)
        try:
            days = float(parts[0].lower().replace("days", "").replace("day", "").strip())
        except ValueError:
            days = 0.0
        s = parts[1].strip() if len(parts) > 1 else "0"
    if ":" in s:
        p = s.split(":")
        try:
            h = float(p[0]); m = float(p[1]) if len(p) > 1 else 0.0
            sec = float(p[2]) if len(p) > 2 else 0.0
            return days * 24 + h + m / 60 + sec / 3600
        except ValueError:
            return days * 24
    try:
        return days * 24 + float(s)
    except ValueError:
        return days * 24


def _vessel_index(rows) -> dict[str, list]:
    idx: dict[str, list] = {}
    for r in rows or []:
        name = pf._norm(r.get("product_name"))
        if not name:
            continue
        batch = (pf._num(r.get("max_batch_size_in_kgs")) or pf._num(r.get("min_batch_size_in_kgs"))
                 or pf._num(r.get("equipment_capacity_in_kgs")) or pf._num(r.get("equipment_capacity_l")))
        cycle = _hours(r.get("cycle_time_in_hrs") or r.get("cycle_time_equipment_with_cleaning_in_hrs")
                       or r.get("cycle_time_equipment_with_out_cleaning_time_in_hrs"))
        idx.setdefault(pf._squash(name), []).append({
            "equipment": pf._norm(r.get("equipment_id")) or "—",
            "organization": pf._norm(r.get("organization")),
            "product_type": pf._norm(r.get("product_type")),
            "division": pf._norm(r.get("division")),
            "batch": round(batch, 1), "cycle_hrs": round(cycle, 2),
        })
    return idx


def _usable_vessels(vlist):
    """Candidate vessels with a real batch size, largest-first (fewest batches)."""
    return sorted([v for v in (vlist or []) if v.get("batch") and v["batch"] > 0],
                  key=lambda v: (-(v["batch"] or 0), v["cycle_hrs"] or 0))


def _earliest_slot(intervals, earliest: date, dur_days: int) -> date:
    """Earliest start >= ``earliest`` where a job of ``dur_days`` fits WITHOUT
    overlapping the equipment's existing busy intervals — i.e. it back-fills idle
    gaps instead of always queuing at the tail. ``intervals`` is a sorted list of
    (start, end) exclusive-end, non-overlapping."""
    dur = timedelta(days=dur_days)
    cand = earliest
    for bs, be in intervals:
        if cand + dur <= bs:      # fits in the gap before this busy block
            return cand
        if cand < be:             # collides — jump past this busy block
            cand = be
    return cand


def _as_date(v):
    if isinstance(v, date):
        return v
    if v is None:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def build_production_schedule(plan_demand, jc_start, soc_rows, stock_rows, bom_path,
                              po_intel, vessel_rows, settings=None, today_iso=None,
                              hours_per_day=24.0, bom_overrides=None) -> dict:
    s = settings or {}
    bom_overrides = bom_overrides or {}
    today = _as_date(today_iso) or date.today()
    jc_start = _as_date(jc_start) or today
    vidx = _vessel_index(vessel_rows)

    # SOC per item -> pending (schedule <= JC start) vs future (> JC start)
    soc: dict[str, dict] = {}
    for r in soc_rows or []:
        k = pf._squash(r.get("ItemDesc"))
        if not k:
            continue
        d = _as_date(r.get("ScheduleDate"))
        qty = pf._num(r.get("Qty"))
        a = soc.setdefault(k, {"pending": 0.0, "future": 0.0, "future_date": None})
        if d and d > jc_start:
            a["future"] += qty
            if a["future_date"] is None or d < a["future_date"]:
                a["future_date"] = d
        else:
            a["pending"] += qty

    # RM stock consolidated by description (Supply-RM logic) + BOM + lead time
    excl = {x.lower() for x in s.get("excluded_subinv", pf.EXCLUDE_SUBINV)}
    rm_by_desc: dict[str, float] = {}
    for r in (stock_rows or []):
        if pf._norm(r.get("SubInv")).lower() in excl:
            continue
        dk = pf._squash(pf._norm(r.get("ItemDesc")))
        if dk:
            rm_by_desc[dk] = rm_by_desc.get(dk, 0.0) + pf._num(r.get("Qty"))
    bom_idx = pf.load_bom_detailed(bom_path) if bom_path else {"by_desc": {}, "by_squash": {}}
    bom_by_desc, bom_by_squash = bom_idx["by_desc"], bom_idx["by_squash"]

    def _selected_bom(name):
        variants = bom_by_desc.get(name.upper()) or bom_by_squash.get(pf._squash(name), [])
        ov = bom_overrides.get(pf._squash(name))
        if ov:   # user-chosen BOM variant persisted on the plan
            chosen = next((v for v in variants if pf._variant_key(v) == ov), None)
            if chosen is not None:
                return chosen
        mfg = [v for v in variants if not v["is_packing"]]
        sel, _ = pf.select_bom(mfg or variants, s)
        return sel

    # build demand segments (per item x SOC scenario). Production Scheduling is for
    # MANUFACTURING items only — repack/relabel, internal and no-BOM are skipped.
    segments = []
    skipped = {"non_manufacturing": 0, "no_bom": 0}
    for row in plan_demand or []:
        name = pf._norm(row.get("item_name"))
        plan_qty = pf._num(row.get("current_jc"))
        if not name or plan_qty <= 0:
            continue
        k = pf._squash(name)
        sel = _selected_bom(name)
        if not sel:
            skipped["no_bom"] += 1
            continue
        if sel.get("bom_class") != "manufacturing":
            skipped["non_manufacturing"] += 1
            continue
        comps = sel["components"]
        sc = soc.get(k, {"pending": 0.0, "future": 0.0, "future_date": None})
        pend = min(sc["pending"], plan_qty)
        fut = min(sc["future"], max(0.0, plan_qty - pend))
        nosoc = max(0.0, plan_qty - pend - fut)
        need_per_unit: dict[str, float] = {}
        lead = 0.0
        for c in comps:
            dk = pf._squash(c["comp_desc"])
            need_per_unit[dk] = need_per_unit.get(dk, 0.0) + c["qty"]
            lt = (po_intel or {}).get(c["comp_code"], {}).get("avg_lead_time_days")
            if lt:
                lead = max(lead, lt)
        vessels = _usable_vessels(vidx.get(k))
        for cls, qty, fdate in (("pending", pend, None), ("future", fut, sc["future_date"]),
                                ("no_soc", nosoc, None)):
            if qty <= 0:
                continue
            segments.append({"item": name, "cls": cls, "qty": round(qty, 1),
                             "future_date": fdate.isoformat() if fdate else None,
                             "need_per_unit": need_per_unit, "lead_days": round(lead, 1),
                             "vessels": vessels, "has_bom": bool(comps)})

    # allocate RM greedily in scenario order (pending -> future -> no_soc), so a
    # segment is "RM available" only if the remaining pool still covers its need
    remaining = dict(rm_by_desc)
    segments.sort(key=lambda x: {"pending": 0, "future": 1, "no_soc": 2}[x["cls"]])
    for seg in segments:
        need = {dk: seg["qty"] * per for dk, per in seg["need_per_unit"].items()}
        if not need:
            rm_ok = True
        else:
            rm_ok = all(remaining.get(dk, 0.0) >= q for dk, q in need.items())
            if rm_ok:
                for dk, q in need.items():
                    remaining[dk] = remaining.get(dk, 0.0) - q
        seg["rm_available"] = rm_ok
        seg["priority"] = PRIORITY[(seg["cls"], rm_ok)]

    def start_basis(seg):
        p = seg["priority"]
        fd = _as_date(seg["future_date"])
        lead_dt = today + timedelta(days=int(math.ceil(seg["lead_days"])))
        if p in (1, 5):
            return max(jc_start, today)
        if p == 2:
            return max(fd or jc_start, today)
        if p == 3:
            return lead_dt
        if p == 4:
            return max(fd or jc_start, lead_dt)
        return lead_dt  # p == 6

    # place on equipment timelines (priority, then start basis). Smart placement:
    # among a product's candidate vessels, pick the one that FINISHES earliest given
    # current equipment load, and BACK-FILL idle gaps (a lower-priority job whose RM
    # is ready can slot into an early opening left by an RM-delayed higher job)
    # instead of always queuing at the tail. equip_busy holds each equipment's sorted
    # busy intervals; busy_hrs/busy_days track load for utilisation.
    segments.sort(key=lambda x: (x["priority"], start_basis(x)))
    equip_busy: dict[str, list] = {}
    busy_hrs: dict[str, float] = {}
    busy_days: dict[str, int] = {}
    jobs, unscheduled = [], []
    for seg in segments:
        cands = seg["vessels"]
        if not cands:
            unscheduled.append({"item": seg["item"], "scenario": SCENARIO[seg["cls"]],
                                "priority": seg["priority"], "qty": seg["qty"],
                                "rm_available": seg["rm_available"], "reason": "no vessel / batch size"})
            continue
        basis = start_basis(seg)
        best = None
        for v in cands:
            batches = max(1, int(math.ceil(seg["qty"] / v["batch"])))
            dur_hrs = batches * (v["cycle_hrs"] or 0)
            dur_days = max(1, int(math.ceil(dur_hrs / hours_per_day))) if dur_hrs > 0 else 1
            sd = _earliest_slot(equip_busy.get(v["equipment"], []), basis, dur_days)
            ed = sd + timedelta(days=dur_days)
            cand = {"v": v, "batches": batches, "dur_hrs": dur_hrs, "dur_days": dur_days, "sd": sd, "ed": ed}
            if best is None or (ed, sd) < (best["ed"], best["sd"]):
                best = cand
        v = best["v"]; equip = v["equipment"]
        insort(equip_busy.setdefault(equip, []), (best["sd"], best["ed"]))
        busy_hrs[equip] = busy_hrs.get(equip, 0.0) + best["dur_hrs"]
        busy_days[equip] = busy_days.get(equip, 0) + best["dur_days"]
        jobs.append({"item": seg["item"], "organization": v["organization"],
                     "product_type": v["product_type"], "equipment": equip,
                     "scenario": SCENARIO[seg["cls"]], "cls": seg["cls"], "priority": seg["priority"],
                     "qty": seg["qty"], "batches": best["batches"], "batch_size": v["batch"],
                     "cycle_hrs": v["cycle_hrs"], "dur_hours": round(best["dur_hrs"], 1),
                     "alt_vessels": len(cands) - 1, "rm_available": seg["rm_available"],
                     "lead_days": seg["lead_days"], "future_date": seg["future_date"],
                     "start": best["sd"].isoformat(), "end": best["ed"].isoformat()})

    pc = Counter(j["priority"] for j in jobs)
    date_from = min((j["start"] for j in jobs), default=None)
    date_to = max((j["end"] for j in jobs), default=None)
    # utilisation over the scheduled horizon (equipment-hours planned vs available)
    horizon_days = ((_as_date(date_to) - _as_date(date_from)).days + 1) if date_from else 0
    n_equip = len(equip_busy)
    capacity_hrs = n_equip * horizon_days * hours_per_day
    total_busy_hrs = sum(busy_hrs.values())
    util_pct = round(100 * total_busy_hrs / capacity_hrs, 1) if capacity_hrs > 0 else 0.0
    eq_items: dict[str, set] = {}
    for j in jobs:
        eq_items.setdefault(j["equipment"], set()).add(j["item"])
    utilisation = sorted(
        [{"equipment": e,
          "busy_hours": round(busy_hrs[e], 1), "busy_days": busy_days.get(e, 0),
          "jobs": sum(1 for j in jobs if j["equipment"] == e),
          "fgs": len(eq_items.get(e, set())),
          "util_pct": round(100 * busy_hrs[e] / (horizon_days * hours_per_day), 1) if horizon_days else 0.0}
         for e in equip_busy], key=lambda x: -x["util_pct"])
    return {
        "jc_start": jc_start.isoformat(), "today": today.isoformat(), "hours_per_day": hours_per_day,
        "jobs": jobs, "unscheduled": unscheduled, "utilisation": utilisation,
        "summary": {
            "scheduled_jobs": len(jobs), "unscheduled_jobs": len(unscheduled),
            "total_batches": sum(j["batches"] for j in jobs),
            "equipment_used": n_equip, "vessel_products": len(vidx),
            "manufacturing_only": True,
            "skipped_non_manufacturing": skipped["non_manufacturing"], "skipped_no_bom": skipped["no_bom"],
            "horizon_days": horizon_days, "planned_hours": round(total_busy_hrs, 1),
            "capacity_hours": round(capacity_hrs, 1), "utilisation_pct": util_pct,
            "by_priority": {p: pc.get(p, 0) for p in range(1, 7)},
            "date_from": date_from, "date_to": date_to,
        },
    }
