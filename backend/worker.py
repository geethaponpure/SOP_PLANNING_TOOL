"""Sync + compute worker — the ONLY component that talks to CRM.

Phase 1 (see ARCHITECTURE.md): pulls the CRM sources the MFG-Stock page needs
(lot-wise stock + item→segment map) into the MySQL staging tables. The API then
serves those tables and never touches CRM at request time.

Usage:
    python worker.py               # run one full sync now, then exit
    python worker.py --loop 1200   # sync now, then every 1200s; also drains the
                                   #   "Refresh now" queue every 30s

(The APScheduler-based scheduling in ARCHITECTURE.md Phase 4 will replace the
simple --loop below; the sync functions here stay the same.)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

from app.integration import crm_sources as crm     # importing app also loads backend/.env
from app.integration import jc_calendar as _jc
from app.integration import msl as _msl
from app.integration import planning_settings as _ps
from app.integration import staging
from app.api.common import _months_ago


def _sync(source: str, fetch, replace) -> int:
    """Run one source sync: log a run, pull from CRM, replace the staging table."""
    run_id = staging.start_run(source)
    t0 = time.time()
    try:
        rows = fetch() or []
        n = replace(rows)
        staging.finish_run(run_id, "ok", n)
        print(f"[sync] {source}: {n} rows in {time.time() - t0:.1f}s")
        return n
    except Exception as e:   # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e).splitlines()[0]}"
        staging.finish_run(run_id, "error", None, msg)
        print(f"[sync] {source} FAILED: {msg[:160]}")
        return -1


def sync_stock_lots() -> int:
    return _sync("stock_lots", crm.stock_lots, staging.replace_stock_lots)


def sync_item_segments() -> int:
    return _sync("item_segments", crm.item_segments, staging.replace_item_segments)


def sync_stock_details() -> int:
    return _sync("stock_details", crm.stock_details, staging.replace_stock_details)


def sync_item_business() -> int:
    return _sync("item_business", crm.item_business, staging.replace_item_business)


def sync_pto_pts() -> int:
    return _sync("pto_pts", crm.pto_pts, staging.replace_pto_pts)


def sync_stock_aged() -> int:
    days = int(_ps.load().get("aged_rm_days", 90))
    return _sync("stock_aged", lambda: crm.stock_details_aged(days), staging.replace_stock_aged)


def sync_vooki_items() -> int:
    biz = _ps.load().get("vooki_business", "Vooki Division")
    return _sync("vooki_items", lambda: crm.vooki_division_items(biz), staging.replace_vooki_items)


def sync_soc_schedule() -> int:
    return _sync("soc_schedule", crm.soc_schedule, staging.replace_soc_schedule)


def sync_dispatch() -> int:
    """Dispatch for the last 3 JCs (dispatch average) and last 13 (MSL)."""
    today = date.today()
    jcs3 = _jc.last_n_jcs(today, 3)
    _sync("dispatch_jc3", lambda: crm.dispatch_by_jc(jcs3),
          lambda rows: staging.replace_dispatch("jc3", rows, len(jcs3)))
    jcs13 = _msl.jc_window()
    return _sync("dispatch_jc13", lambda: crm.dispatch_by_jc(jcs13),
                 lambda rows: staging.replace_dispatch("jc13", rows, len(jcs13)))


SYNCS = [sync_item_segments, sync_stock_lots, sync_stock_details,
         sync_item_business, sync_pto_pts,
         sync_stock_aged, sync_vooki_items, sync_soc_schedule, sync_dispatch]


# ── context-keyed sources (content depends on today's planning context) ───────

def compute_context() -> dict:
    """The planning context the API derives at request time — plan JC, accounting
    year, SOC window, freeze date, in-transit window. Same jc_calendar logic the
    API uses, so the worker stages exactly the slice the API will read."""
    today = date.today()
    s = _ps.load()
    pj = _jc.planning_jc_entry(today) or _jc.current_jc_entry(today) or {}
    plan_jc = pj.get("jc") or _jc.current_jc(today)
    fy = _jc.fiscal_year(today)
    acc_year = os.getenv("BP_ACCYEAR") or pj.get("fy") or f"{fy}-{fy + 1}"
    soc_from, soc_to = _jc.soc_window(today, int(s.get("soc_window_months", 0)))
    freeze = _jc.active_freeze(today) or today.isoformat()
    itr_from = _months_ago(today, int(s.get("intransit_po_months", 4) or 4))
    return {"plan_jc": plan_jc, "acc_year": acc_year,
            "soc_from": str(soc_from), "soc_to": str(soc_to), "freeze_date": freeze,
            "intransit_from": str(itr_from),
            "blanket_po_qty": float(s.get("blanket_po_qty", 500000) or 0),
            "mfg_orgs": s.get("mfg_soc_orgs")}


def sync_projection(ctx) -> int:
    return _sync("projection",
                 lambda: crm.business_plan_projection(ctx["acc_year"], ctx["plan_jc"]),
                 lambda rows: staging.replace_projection(ctx["acc_year"], ctx["plan_jc"], rows))


def sync_soc_pending(ctx) -> int:
    def fetch():
        return {"all": crm.despatch_pending(ctx["soc_from"], ctx["soc_to"]) or [],
                "mfg": crm.despatch_pending_mfg(ctx["soc_from"], ctx["soc_to"], ctx["mfg_orgs"]) or []}
    return _sync("soc_pending", fetch, staging.replace_soc_pending)


def sync_soc_detail(ctx) -> int:
    return _sync("soc_detail", lambda: crm.soc_detail(ctx["freeze_date"]), staging.replace_soc_detail)


def sync_intransit(ctx) -> int:
    return _sync("intransit",
                 lambda: crm.po_open_intransit_detail(ctx["intransit_from"], ctx["blanket_po_qty"]),
                 staging.replace_intransit)


CONTEXT_SYNCS = [sync_projection, sync_soc_pending, sync_soc_detail, sync_intransit]


def compute_rm_planning() -> int:
    """Phase 3: run the heavy RM-Plan build (planning_filter / BOM explosion) from
    the freshly-synced staging tables and store the result, so the RM-Plan page
    loads instantly instead of computing on the request."""
    from app.api.live import _build_rm
    from fastapi.encoders import jsonable_encoder
    run_id = staging.start_run("compute_rm_planning")
    t0 = time.time()
    try:
        rp = _build_rm()
        n = len(rp.get("products", []))
        staging.save_computed("rm_planning", jsonable_encoder(rp), n)
        staging.finish_run(run_id, "ok", n)
        print(f"[compute] rm_planning: {n} products in {time.time() - t0:.1f}s")
        return n
    except Exception as e:   # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e).splitlines()[0]}"
        staging.finish_run(run_id, "error", None, msg)
        print(f"[compute] rm_planning FAILED: {msg[:160]}")
        return -1


def run_all() -> None:
    print("[worker] full sync starting…")
    for fn in SYNCS:
        fn()
    ctx = compute_context()
    staging.write_context(ctx)
    print(f"[worker] context: planning JC{ctx['plan_jc']} {ctx['acc_year']} · "
          f"SOC {ctx['soc_from']}..{ctx['soc_to']} · freeze {ctx['freeze_date']}")
    for fn in CONTEXT_SYNCS:
        fn(ctx)
    compute_rm_planning()   # Phase 3: precompute the RM plan from the fresh staging
    print("[worker] full sync done.")


def _drain_requests() -> None:
    """Run a full sync if any 'Refresh now' request is pending."""
    if staging.claim_pending_requests():
        print("[worker] refresh requested → syncing")
        run_all()


def _schedule(interval: int) -> None:
    """Recommended runner: APScheduler does a full CRM sync every `interval`
    seconds and drains the Refresh-now queue every 30s. Blocks (dedicated
    worker process)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    run_all()   # sync once at boot so staging is warm immediately
    sched = BlockingScheduler()
    sched.add_job(run_all, "interval", seconds=interval, id="full_sync",
                  max_instances=1, coalesce=True)
    sched.add_job(_drain_requests, "interval", seconds=30, id="drain_refresh",
                  max_instances=1, coalesce=True)
    print(f"[worker] scheduler running: full sync every {interval}s · "
          f"refresh-drain every 30s (Ctrl+C to stop)")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("[worker] scheduler stopped.")


def _loop(interval: int) -> None:
    """Dependency-free fallback scheduler (no APScheduler): full sync every
    `interval` seconds, draining the Refresh-now queue every ~30s in between."""
    run_all()
    next_full = time.time() + interval
    while True:
        time.sleep(30)
        if staging.claim_pending_requests():
            print("[worker] refresh requested → syncing")
            run_all()
            next_full = time.time() + interval
        elif time.time() >= next_full:
            run_all()
            next_full = time.time() + interval


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--schedule":
        _schedule(int(sys.argv[2]) if len(sys.argv) >= 3 else 1200)
    elif len(sys.argv) >= 3 and sys.argv[1] == "--loop":
        _loop(int(sys.argv[2]))
    else:
        run_all()
