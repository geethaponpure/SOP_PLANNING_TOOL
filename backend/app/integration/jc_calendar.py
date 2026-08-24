"""JC (Job Cycle) calendar for the planning tool.

A fiscal year is divided into 13 JCs of approximately 4 weeks each
(based on the PPC JC Calendar). This module provides the calendar for
the current fiscal year and helpers used by jc_plan.py and ppv.py.

FY runs April to March (Indian fiscal year).
Each JC ≈ 4 weeks; JC1 starts in April, JC13 ends in March.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from functools import lru_cache


# ── JC calendar definition ───────────────────────────────────────────────────
# Approximate JC boundaries for FY 2026-2027 (Apr 2026 – Mar 2027).
# Adjust via JC_CALENDAR_JSON env var (path to a JSON file) or these defaults.

# Each entry: jc number, start date, end date, approximate working days
_FY2627_JCS = [
    (1,  date(2026, 4,  1), date(2026, 4, 25), 19),
    (2,  date(2026, 4, 26), date(2026, 5, 23), 20),
    (3,  date(2026, 5, 24), date(2026, 6, 20), 20),
    (4,  date(2026, 6, 21), date(2026, 7, 18), 20),
    (5,  date(2026, 7, 19), date(2026, 8, 15), 20),
    (6,  date(2026, 8, 16), date(2026, 9, 12), 20),
    (7,  date(2026, 9, 13), date(2026,10, 10), 20),
    (8,  date(2026,10, 11), date(2026,11,  7), 20),
    (9,  date(2026,11,  8), date(2026,12,  5), 20),
    (10, date(2026,12,  6), date(2027,  1,  2), 20),
    (11, date(2027,  1,  3), date(2027,  1, 30), 20),
    (12, date(2027,  1, 31), date(2027,  2, 27), 20),
    (13, date(2027,  2, 28), date(2027,  3, 31), 22),
]

# FY 2025-2026 (historical, for PPV standard year matching)
_FY2526_JCS = [
    (1,  date(2025, 4,  1), date(2025, 4, 25), 19),
    (2,  date(2025, 4, 26), date(2025, 5, 23), 20),
    (3,  date(2025, 5, 24), date(2025, 6, 20), 20),
    (4,  date(2025, 6, 21), date(2025, 7, 18), 20),
    (5,  date(2025, 7, 19), date(2025, 8, 15), 20),
    (6,  date(2025, 8, 16), date(2025, 9, 12), 20),
    (7,  date(2025, 9, 13), date(2025,10, 10), 20),
    (8,  date(2025,10, 11), date(2025,11,  7), 20),
    (9,  date(2025,11,  8), date(2025,12,  5), 20),
    (10, date(2025,12,  6), date(2026,  1,  2), 20),
    (11, date(2026,  1,  3), date(2026,  1, 30), 20),
    (12, date(2026,  1, 31), date(2026,  2, 27), 20),
    (13, date(2026,  2, 28), date(2026,  3, 31), 22),
]

# FY 2024-2025 (for 2-year PO history)
_FY2425_JCS = [
    (1,  date(2024, 4,  1), date(2024, 4, 26), 19),
    (2,  date(2024, 4, 27), date(2024, 5, 24), 20),
    (3,  date(2024, 5, 25), date(2024, 6, 21), 20),
    (4,  date(2024, 6, 22), date(2024, 7, 19), 20),
    (5,  date(2024, 7, 20), date(2024, 8, 16), 20),
    (6,  date(2024, 8, 17), date(2024, 9, 13), 20),
    (7,  date(2024, 9, 14), date(2024,10, 11), 20),
    (8,  date(2024,10, 12), date(2024,11,  8), 20),
    (9,  date(2024,11,  9), date(2024,12,  6), 20),
    (10, date(2024,12,  7), date(2025,  1,  3), 20),
    (11, date(2025,  1,  4), date(2025,  1, 31), 20),
    (12, date(2025,  2,  1), date(2025,  2, 28), 20),
    (13, date(2025,  3,  1), date(2025,  3, 31), 22),
]


@lru_cache(maxsize=1)
def _all_jcs() -> list[dict]:
    """All defined JC entries across all loaded FYs."""
    entries = []
    for fy_jcs in (_FY2425_JCS, _FY2526_JCS, _FY2627_JCS):
        for (jc, start, end, wdays) in fy_jcs:
            fy = f"{start.year if start.month >= 4 else start.year - 1}-{(start.year + 1) if start.month >= 4 else start.year}"
            # planning freeze = 2nd day of the 3rd week of the JC (start + 15 days),
            # capped at the JC end. Orders after this are evaluated for Adhoc.
            freeze = min(start + timedelta(days=15), end)
            entries.append({
                "jc":           jc,
                "start":        start,
                "end":          end,
                "working_days": wdays,
                "fy":           fy,
                "freeze":       freeze,
                # display/serialisation keys consumed by jc_plan.py
                "label":        f"JC{jc}",
                "from":         start.isoformat(),
                "to":           end.isoformat(),
                "freeze_date":  freeze.isoformat(),
            })
    return entries


def calendar() -> list[dict]:
    """Return the JC calendar for the current fiscal year."""
    today = date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1
    fy = f"{fy_start}-{fy_start + 1}"
    return [j for j in _all_jcs() if j["fy"] == fy]


def all_jcs() -> list[dict]:
    """Return all JC entries across all FYs (for PPV/Scorecard multi-year lookups)."""
    return _all_jcs()


def horizon(today: date | None = None) -> list[dict]:
    """Return the forward-looking JC horizon from today (current + future JCs this FY)."""
    today = today or date.today()
    return [j for j in calendar() if j["end"] >= today]


def jc_for_date(d: date) -> dict | None:
    """Return the JC entry that contains the given date (across all FYs)."""
    for j in _all_jcs():
        if j["start"] <= d <= j["end"]:
            return j
    return None


def jc_number_for_date(d: date) -> int | None:
    """Return just the JC number for a date (1-13)."""
    j = jc_for_date(d)
    return j["jc"] if j else None


def seasonal_factor(jc_entry: dict, seasonal_indices: list[float]) -> float:
    """Return the seasonal index for the month in which a JC mid-point falls."""
    mid = jc_entry["start"] + (jc_entry["end"] - jc_entry["start"]) / 2
    month_idx = mid.month - 1   # 0-based Jan..Dec
    if not seasonal_indices:
        return 1.0
    return seasonal_indices[month_idx % 12]


# ── live-CRM RM-planning helpers (Supply & RM / PPV / dispatch) ────────────────

def fiscal_year(d: date) -> int:
    """Start calendar-year of the fiscal year (April-March) containing ``d``."""
    return d.year if d.month >= 4 else d.year - 1


def fiscal_label(d: date) -> str:
    fy = fiscal_year(d)
    return f"{fy}-{str(fy + 1)[2:]}"


def fiscal_jc(d: date) -> int:
    """Approximate JC number (1-13) of a date within its fiscal year (28-day
    cycles from 1 April). Buckets multi-year PO history into JCs."""
    start = date(fiscal_year(d), 4, 1)
    return min(13, max(1, (d - start).days // 28 + 1))


def current_jc(today: date | None = None) -> int:
    today = today or date.today()
    j = jc_for_date(today)
    if j:
        return j["jc"]
    return 1 if today < date(fiscal_year(today), 4, 1) else 13


def current_jc_entry(today: date | None = None) -> dict | None:
    """The full JC entry (with freeze date) containing ``today``."""
    return jc_for_date(today or date.today())


def third_week_monday(jc_entry: dict) -> date:
    """The Monday of the JC's 3rd week — the day CRM compiles/approves the NEXT
    JC's projection. From this day, planning shifts forward to the next JC."""
    start = jc_entry["start"]
    first_mon = start + timedelta(days=(7 - start.weekday()) % 7)  # Mon=0
    return first_mon + timedelta(days=14)


def planning_jc_entry(today: date | None = None) -> dict | None:
    """The JC to PLAN for. Advances to the next JC from the current JC's 3rd-week
    Monday (when its projection is compiled/approved in CRM). Example: on the
    3rd-week Monday of JC4 the system becomes available for JC5 planning."""
    today = today or date.today()
    cur = jc_for_date(today)
    if not cur:
        return None
    if today >= third_week_monday(cur):
        nxt = next((j for j in _all_jcs()
                    if j["fy"] == cur["fy"] and j["jc"] == cur["jc"] + 1), None)
        if nxt:
            return nxt
    return cur


def planning_jc(today: date | None = None) -> int:
    """JC number the system should be planning for (see ``planning_jc_entry``)."""
    j = planning_jc_entry(today)
    return j["jc"] if j else current_jc(today)


def freeze_date(today: date | None = None) -> str | None:
    """Planning freeze date (ISO) of the JC containing ``today``."""
    j = jc_for_date(today or date.today())
    return j["freeze_date"] if j else None


def active_freeze(today: date | None = None) -> str | None:
    """Most recent freeze date on/before ``today`` (current or previous JC's
    freeze). Open SOC orders after this date are the Adhoc candidates."""
    today = today or date.today()
    past = [j for j in _all_jcs() if j["freeze"] <= today]
    return max(past, key=lambda j: j["freeze"])["freeze_date"] if past else None


def master_rows() -> list[dict]:
    """All JC calendar entries as JC_MASTER rows (for seeding the DB)."""
    return [{"fy": j["fy"], "jc_number": j["jc"], "start_date": j["from"],
             "end_date": j["to"], "freeze_date": j["freeze_date"]} for j in _all_jcs()]


def _entry(j: dict) -> dict:
    return {"jc": j["jc"], "from": j["start"].isoformat(), "to": j["end"].isoformat()}


def previous_jc(today: date | None = None) -> dict | None:
    today = today or date.today()
    cur = current_jc(today)
    j = next((x for x in calendar() if x["jc"] == cur - 1), None)
    return _entry(j) if j else None


def last_n_jcs(today: date | None = None, n: int = 3) -> list[dict]:
    """The ``n`` JCs immediately before the current one (oldest first), each as
    {jc, from, to} -- used for the trailing dispatch average."""
    today = today or date.today()
    cur = current_jc(today)
    return [_entry(j) for j in calendar() if cur - n <= j["jc"] <= cur - 1]


def _minus_months(d: date, months: int) -> date:
    import calendar as _cal
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def soc_window(today: date | None = None, months: int = 0) -> tuple[str, str]:
    """Pending-SOC window for the PLANNING JC: up to the close of the JC immediately
    before the planning JC (the day before the planning JC starts). When the plan has
    rolled forward (e.g. planning JC5), this runs up to JC4's close — keeping pending
    SOC in step with the planning JC rather than the calendar JC.

    ``months`` sets the look-back start: months <= 0 means "as on date" — ALL open
    pending SOC up to that close, with no lower bound; months > 0 keeps the legacy
    behaviour of starting ``months`` months before the close."""
    today = today or date.today()
    pj = planning_jc_entry(today)
    prev = None
    if pj:
        prev = next((j for j in _all_jcs()
                     if j["fy"] == pj["fy"] and j["jc"] == pj["jc"] - 1), None)
    prev = prev or previous_jc(today)   # fallback for out-of-calendar dates
    end = date.fromisoformat(prev["to"]) if prev else date(fiscal_year(today), 4, 1) - timedelta(days=1)
    start = date(1900, 1, 1) if months <= 0 else _minus_months(end, months)
    return start.isoformat(), end.isoformat()
