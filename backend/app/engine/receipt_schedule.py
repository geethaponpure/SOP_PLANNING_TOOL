"""Item Receipt Schedule (blueprint extension).

Turns a saved plan's production schedule into a *receipt* view for Planner /
Warehouse / Production / QC / BU / Branch users:

  • when each planned FG is **available in the warehouse**
    = manufacturing completion date + a standard lead time (default 3 days), and
  • for a chosen branch region, when it would be **received at the branch**
    = warehouse-available date + that region's logistic lead time.

Windows W1..W4 are the next four JC-calendar Job Cycles (each already dated);
every item is bucketed into the window its relevant date falls in. Pure
functions only — the API layer (main.py) supplies the production schedule,
the JC horizon and the admin-configured lead times.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta


def _to_date(s) -> date | None:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _windows(jcs: list[dict]) -> list[dict]:
    """The four JC windows W1..W4 from the forward JC horizon."""
    out = []
    for i, j in enumerate(jcs[:4], start=1):
        out.append({
            "key":  f"W{i}",
            "jc":   j.get("label") or f"JC{j.get('jc')}",
            "from": j.get("from"),
            "to":   j.get("to"),
        })
    return out


def _window_for(d: date | None, windows: list[dict]) -> str:
    """Which W-key a date lands in (clamped: before W1 -> W1, after last -> last)."""
    if not windows or d is None:
        return "—"
    first_from = _to_date(windows[0]["from"])
    if first_from and d < first_from:
        return windows[0]["key"]
    for w in windows:
        wf, wt = _to_date(w["from"]), _to_date(w["to"])
        if wf and wt and wf <= d <= wt:
            return w["key"]
    return windows[-1]["key"]   # beyond the horizon -> last window


def build_receipt_schedule(prod: dict, jcs: list[dict], std_lead_days: int,
                           region: str, logistic_leads: dict) -> dict:
    """Receipt schedule for one plan's production jobs.

    prod            -- result of build_production_schedule (jobs with start/end + rm_available)
    jcs             -- forward JC horizon (jc_calendar.horizon()); first 4 become W1..W4
    std_lead_days   -- standard lead time (days) from manufacturing completion to warehouse
    region          -- selected branch region (South/North/West/East)
    logistic_leads  -- {region: logistic lead days}
    """
    windows = _windows(jcs)
    std_lead_days = int(std_lead_days)
    logistic = int((logistic_leads or {}).get(region, 0))
    items = []
    for j in prod.get("jobs", []):
        # manufacturing completes at `end`; fall back to `start` if a job has no end
        mfg_end = _to_date(j.get("end")) or _to_date(j.get("start"))
        if not mfg_end:
            continue
        wh_date = mfg_end + timedelta(days=std_lead_days)
        branch_date = wh_date + timedelta(days=logistic)
        items.append({
            "item":               j.get("item"),
            "organization":       j.get("organization"),
            "qty":                j.get("qty"),
            "rm_available":       bool(j.get("rm_available")),
            "mfg_start":          j.get("start"),
            "mfg_end":            mfg_end.isoformat(),
            "std_lead_days":      std_lead_days,
            "warehouse_date":     wh_date.isoformat(),
            "warehouse_window":   _window_for(wh_date, windows),
            "region":             region,
            "logistic_lead_days": logistic,
            "branch_date":        branch_date.isoformat(),
            "branch_window":      _window_for(branch_date, windows),
        })
    items.sort(key=lambda r: (r["warehouse_date"], r["item"] or ""))

    wh_counts = Counter(r["warehouse_window"] for r in items)
    br_counts = Counter(r["branch_window"] for r in items)
    return {
        "windows":            windows,
        "items":              items,
        "region":             region,
        "std_lead_days":      std_lead_days,
        "logistic_lead_days": logistic,
        "summary": {
            "total_items":         len(items),
            "rm_available_items":  sum(1 for r in items if r["rm_available"]),
            "warehouse_by_window": {w["key"]: wh_counts.get(w["key"], 0) for w in windows},
            "branch_by_window":    {w["key"]: br_counts.get(w["key"], 0) for w in windows},
        },
    }
