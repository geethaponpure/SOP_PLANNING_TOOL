"""FastAPI application — S&OP Planning Tool backend."""
from __future__ import annotations

import io
import json
import os
from collections import Counter
from datetime import datetime
from functools import lru_cache

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .data import build_dataset
from . import db_state
from .engine import baseline as bl
from .engine import dq as dq_engine
from .engine import segmentation as seg
from .engine import supply as sp
from .engine import validation as val
from .engine import forecasting as fc
from .engine import analytics
from .engine import kpis
from .engine import governance
from .engine.jc_plan import build_jc_plan
from .engine.adhoc import build_adhoc_plan
from .engine.ppv import build_ppv
from .engine import receipt_schedule as _rsched
from .engine.supplier_scorecard import build_supplier_scorecard

# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="S&OP Planning Tool", version="2.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
db_state.init()

from .srdms_api import router as _srdms_router   # noqa: E402
app.include_router(_srdms_router)
from .user_master_api import router as _um_router   # noqa: E402
app.include_router(_um_router)
from .roles_api import router as _roles_router   # noqa: E402
app.include_router(_roles_router)

# ── cached helpers ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_data():
    return build_dataset()


@lru_cache(maxsize=1)
def _get_baselines():
    data = _get_data()
    return {
        sid: bl.build_baseline(sku, data["history"][sid],
                               data["history_periods"], data["cycle_period"])
        for sid, sku in data["skus"].items()
    }


@lru_cache(maxsize=1)
def _get_segmentation():
    return seg.build_segmentation(_get_data(), _get_baselines())


@lru_cache(maxsize=1)
def _get_dq():
    return dq_engine.build_dq(_get_data())


@lru_cache(maxsize=1)
def _get_supply():
    data      = _get_data()
    bases     = _get_baselines()
    segm      = _get_segmentation()
    dq_r      = _get_dq()
    blocked   = set(dq_r.get("blocked_skus", []))
    consensus = {sid: bases[sid]["baseline"] for sid in data["skus"]}
    return sp.build_supply_plan(data, bases, segm, consensus, blocked)


def _get_cycle():
    cycle = _get_data()["cycle_period"]
    lock_meta, consensus = db_state.load_lock(cycle)
    return cycle, lock_meta, consensus


def _get_consensus():
    _, _, consensus = _get_cycle()
    if consensus:
        return consensus
    return {sid: _get_baselines()[sid]["baseline"] for sid in _get_data()["skus"]}


# ── live-CRM RM / purchasing features ─────────────────────────────────────────
from datetime import date as _date
from .integration import planning_filter as _pf
from .integration import planning_settings as _ps
from .integration import crm_sources as _crm
from .integration import jc_calendar as _jc
from .integration import mysql_db as _mysql
from .integration import scheduling as _sched
from .integration import rm_consumption as _rmc
from .integration import projection_accuracy as _pacc
from .integration import msl as _msl
from .integration.adapter import _resolve_file, _resolve_po_files, resolve_latest_po_register
from . import publish as _pub


def _live() -> bool:
    return os.getenv("DATA_SOURCE", "synthetic").lower() == "live"


def _try(fn, label):
    try:
        return fn()
    except Exception as e:   # noqa: BLE001
        print(f"[live] {label}: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
        return None


@lru_cache(maxsize=1)
def _business_map():
    m: dict = {}
    if not _live():
        return m
    rows = _try(_crm.item_business, "item_business") or []
    for r in rows:
        code, biz = _pf._norm(r.get("ItemCode")), _pf._norm(r.get("Business"))
        if code and biz and (code not in m or biz.lower() == "raw material"):
            m[code] = biz
    return m


@lru_cache(maxsize=1)
def _item_segments():
    """{item_code -> (Division, Segment2, Segment3)} from CRM ItemCategories.
    Division = Segment1, preferring a target division ('Performance Chemicals'/'NPD')
    when the item is categorised under more than one."""
    m: dict = {}
    if not _live():
        return m
    for r in (_try(_crm.item_segments, "item_segments") or []):
        code = _pf._norm(r.get("ItemCode"))
        if code:
            division = _pf._norm(r.get("DivisionTarget")) or _pf._norm(r.get("Segment1"))
            m[code] = (division, _pf._norm(r.get("Segment2")), _pf._norm(r.get("Segment3")))
    return m


@lru_cache(maxsize=1)
def _division_by_name():
    """{squash(item description) -> Division (Segment1)} from CRM ItemCategories, used to
    scope the RM plan to a division. A description is tagged 'Performance Chemicals' when
    ANY of its codes is categorised under it (PC wins), so the PC scope is inclusive."""
    m: dict = {}
    if not _live():
        return m
    for r in (_try(_crm.item_segments, "item_segments") or []):
        nm = _pf._squash(r.get("ItemName"))
        if not nm:
            continue
        div = _pf._norm(r.get("DivisionTarget")) or _pf._norm(r.get("Segment1"))
        if div and m.get(nm) != "Performance Chemicals":
            m[nm] = div
    return m


# shared heavy loaders -- read each big source ONCE and reuse across pages
@lru_cache(maxsize=1)
def _crm_stock():
    return _try(_crm.stock_details, "stock") or []


@lru_cache(maxsize=1)
def _stock_lots_audit():
    """Lot-wise stock (item/org/subinv/lot) for the audit sheets — excluded
    sub-inventories dropped, each row tagged Warehouse vs Branch (same rule as the
    plan's stock)."""
    if not _live():
        return []
    s = _ps.load()
    warehouse = set(s.get("warehouse_orgs", _pf.WAREHOUSE_ORGS))
    excluded = {x.lower() for x in s.get("excluded_subinv", _pf.EXCLUDE_SUBINV)}
    segmap = _item_segments()   # {code -> (Division, Segment2, Segment3)}
    out = []
    for r in (_try(_crm.stock_lots, "stock_lots") or []):
        subinv = _pf._norm(r.get("SubInv"))
        if subinv.lower() in excluded:
            continue
        qty = _pf._num(r.get("Qty"))
        if qty == 0:
            continue
        org = _pf._norm(r.get("Organization"))
        code = _pf._norm(r.get("ItemCode"))
        seg = segmap.get(code) or ("", "", "")
        out.append({"item_code": code, "item_desc": _pf._norm(r.get("ItemDesc")),
                    "org": org, "org_code": _pf._norm(r.get("OrgCode")), "subinv": subinv,
                    "lot": _pf._norm(r.get("Lot")), "qty": round(qty, 1),
                    "segment2": seg[1], "segment3": seg[2],
                    "age_days": r.get("AgeDays"), "aging_date": str(r.get("AgingDate") or "")[:10],
                    "location": "warehouse" if org in warehouse else "branch"})
    return out


@lru_cache(maxsize=1)
def _intransit_lots_audit():
    """Per-PO-line in-transit detail (the audit trail behind the in-transit totals) —
    same recency/blanket/inter-company scoping as the plan. Empty for the file source."""
    if not _live():
        return []
    s = _ps.load()
    if s.get("intransit_source", "crm") != "crm":
        return []
    from_date = _months_ago(_date.today(), int(s.get("intransit_po_months", 4) or 4))
    blanket = float(s.get("blanket_po_qty", 500000) or 0)
    rows = _try(lambda: _crm.po_open_intransit_detail(from_date, blanket), "po_intransit_detail") or []
    exclude = _pf._intercompany_set(s)
    bmap = _business_map()
    rm_label = s.get("raw_material_business", "Raw Material")
    rm_only_orgs = {_pf._norm(o).upper() for o in s.get("intransit_rm_only_orgs", []) if o}
    out = []
    for r in rows:
        if _pf._norm(r.get("Vendor_Name")).upper() in exclude:
            continue
        code = _pf._norm(r.get("Item_Code"))
        biz = _pf._norm(bmap.get(code, ""))
        org = _pf._norm(r.get("Org_Name"))
        # RM-only orgs (e.g. Madhavaram): drop non-Raw-Material business (GC1/GC2 …)
        if org.upper() in rm_only_orgs and not _pf._is_raw_material(biz, rm_label):
            continue
        out.append({
            "item_code": code, "item_desc": _pf._norm(r.get("Item_Desc")), "business": biz,
            "po_number": _pf._norm(r.get("Po_Number")), "po_date": str(r.get("Po_Date") or "")[:10],
            "vendor": _pf._norm(r.get("Vendor_Name")), "org": org,
            "procurement_type": _pf._norm(r.get("Procurement_Type")),
            "ordered": round(_pf._num(r.get("Quantity")), 1), "received": round(_pf._num(r.get("Received")), 1),
            "cancelled": round(_pf._num(r.get("Cancelled")), 1), "in_transit": round(_pf._num(r.get("InTransit")), 1),
        })
    return out


def _intransit_unmatched(rp):
    """Open-PO in-transit items NOT matched to any planned BOM RM (by item code or
    description) — for validation. Surfaces items bought/in-transit but absent from
    every recipe, or a code/description mismatch (e.g. OLKLIN-5240, Mg Chloride Crystal)."""
    lots = _intransit_lots_audit()
    if not lots:
        return []
    used_codes, used_descs = set(), set()
    for key in ("consolidated_rm", "consolidated_rm_packing"):
        for x in rp.get(key, []):
            used_codes.update(x.get("rm_codes", []))
            used_descs.add(_pf._squash(x.get("rm_desc", "")))
    agg: dict = {}
    for lt in lots:
        code, desc = lt.get("item_code"), lt.get("item_desc")
        if code in used_codes or _pf._squash(desc) in used_descs:
            continue
        k = code or _pf._squash(desc)
        a = agg.setdefault(k, {"item_code": code, "item_desc": desc, "in_transit": 0.0,
                               "po_count": 0, "vendors": set(), "latest_po": ""})
        a["in_transit"] += lt.get("in_transit", 0.0)
        a["po_count"] += 1
        if lt.get("vendor"):
            a["vendors"].add(lt["vendor"])
        if (lt.get("po_date") or "") > a["latest_po"]:
            a["latest_po"] = lt.get("po_date") or ""
    out = [{"item_code": v["item_code"], "item_desc": v["item_desc"],
            "in_transit": round(v["in_transit"], 1), "po_count": v["po_count"],
            "latest_po": v["latest_po"], "vendors": sorted(v["vendors"])[:5]}
           for v in agg.values()]
    out.sort(key=lambda r: -r["in_transit"])
    return out


@lru_cache(maxsize=1)
def _dispatch3():
    jcs = _jc.last_n_jcs(_date.today(), 3)
    rows = _try(lambda: _crm.dispatch_by_jc(jcs), "dispatch") or []
    return rows, len(jcs)


@lru_cache(maxsize=1)
def _po_rows():
    po = _resolve_po_files()
    return _try(lambda: _pf.read_po_rows(po), "po_rows") or [] if po else []


@lru_cache(maxsize=1)
def _po_intel():
    rows = _po_rows()
    return _pf.load_po_intel(None, rows=rows) if rows else None


@lru_cache(maxsize=1)
def _po_pending():
    """Pending / in-transit from the NEWEST PO register snapshot (PO_receipts/) —
    Oracle's authoritative current open-PO view (reflects latest status: a PO fully
    received in a later download is closed/absent). Lead-time uses ALL files
    (de-duplicated by Receipt No.); pending stays on the latest register to avoid
    resurrecting old POs whose earlier receipts aren't in the local files."""
    reg = resolve_latest_po_register()
    if not reg:
        return None
    rows = _try(lambda: _pf.read_po_rows(reg), "po_register") or []
    return _pf.load_po_pending_rows(rows) if rows else None


def _months_ago(d, n):
    """Date n whole months before d (day clamped to 28 to stay valid)."""
    m, y = d.month - int(n), d.year
    while m <= 0:
        m += 12
        y -= 1
    return _date(y, m, min(d.day, 28))


def _po_intransit(s, today):
    """In-transit (open-PO) per settings: live CRM BiPoDetails balance (default) —
    ordered − received − cancelled, for POs placed within `intransit_po_months` and
    ordered ≤ `blanket_po_qty`, inter-company vendors + RM-only-org non-RM business
    dropped — else the legacy file receipts register. Aggregated from the same per-line
    detail as the audit sheets, so the planning total reconciles with them exactly."""
    if s.get("intransit_source", "crm") == "crm":
        lots = _intransit_lots_audit()
        if lots:
            agg: dict = {}
            for lt in lots:
                code = lt.get("item_code")
                if not code:
                    continue
                a = agg.setdefault(code, {"name": lt.get("item_desc", ""), "in_transit": 0.0,
                                          "received": 0.0, "open_pos": 0, "pend_dates": []})
                if not a["name"]:
                    a["name"] = lt.get("item_desc", "")
                a["in_transit"] += lt.get("in_transit", 0.0)
                a["received"] += lt.get("received", 0.0)
                a["open_pos"] += 1
            from_date = _months_ago(today, int(s.get("intransit_po_months", 4) or 4))
            return agg, from_date
    return _po_pending(), None


def _po_clean_rows():
    """Map the de-duplicated PO rows into clean, typed dicts for the DB."""
    out = []
    for r in (_po_rows() or []):
        code = _pf._norm(r.get("Item Code"))
        if not code:
            continue
        out.append({
            "receipt_no": _pf._norm(r.get("Receipt No.")), "po_number": _pf._norm(r.get("Po Number")),
            "item_code": code, "item_desc": _pf._norm(r.get("Item Description")),
            "po_date": _pf._parse_date(r.get("Po Date")), "receipt_date": _pf._parse_date(r.get("Receipt Date")),
            "po_qty": _pf._num(r.get("Po Quantity")), "receipt_qty": _pf._num(r.get("Receipt Qty")),
            "vendor_name": _pf._norm(r.get("Vendor Name")), "org_name": _pf._norm(r.get("Org Name")),
            "subinventory": _pf._norm(r.get("Subinventory")), "lot_number": _pf._norm(r.get("Lot Number")),
            "currency": _pf._norm(r.get("Currency Code")), "unit_price": _pf._num(r.get("Po Unit Price")),
        })
    return out


@lru_cache(maxsize=1)
def _po_ingest():
    """Upload the de-duplicated PO receipts to the DB (PO_RECEIPTS). Idempotent."""
    rows = _po_clean_rows()
    if not rows:
        return {"ok": True, "written": 0}
    res = _mysql.ingest_po_receipts(rows)
    print(f"[po] DB ingest: {res.get('written')} rows" if res.get("ok")
          else f"[po] DB ingest FAILED: {res.get('error')}")
    return res


_po_last_sig = [None]


def _po_sig():
    sig = []
    for f in _resolve_po_files():
        try:
            sig.append((f, os.path.getmtime(f)))
        except OSError:
            pass
    return tuple(sorted(sig))


def _ensure_po_fresh():
    """When files are added/changed in PO_receipts/, refresh lead-time + pending and
    re-upload to the DB — so adding a file here updates everything automatically."""
    sig = _po_sig()
    if sig != _po_last_sig[0]:
        _po_last_sig[0] = sig
        for fn in (_po_rows, _po_intel, _po_pending, _po_ingest):
            fn.cache_clear()
        _po_ingest()


def _build_rm(overrides=None, plan_mode="crm", bom_overrides=None):
    if not _live():
        return {"products": [], "consolidated_rm": [], "summary": {},
                "note": "Requires DATA_SOURCE=live."}
    _ensure_po_fresh()   # pick up any newly-added PO_receipts/ files + re-upload to DB
    s = _ps.load()
    today = _date.today()
    # Which JC to plan: advances to the next JC from the current JC's 3rd-week Monday
    # (when CRM compiles/approves that next JC's projection). Example: on JC4's
    # 3rd-week Monday the system becomes available for JC5 planning.
    pj = _jc.planning_jc_entry(today) or _jc.current_jc_entry(today) or {}
    plan_jc = pj.get("jc") or _jc.current_jc(today)
    _fy = _jc.fiscal_year(today)
    acc_year = os.getenv("BP_ACCYEAR") or pj.get("fy") or f"{_fy}-{_fy + 1}"
    win = _jc.soc_window(today, int(s.get("soc_window_months", 0)))
    stock_rows = _crm_stock() or None
    pending_rows = _try(lambda: _crm.despatch_pending(win[0], win[1]), "pending")
    mfg_pending_rows = _try(lambda: _crm.despatch_pending_mfg(win[0], win[1], s.get("mfg_soc_orgs")),
                            "mfg_pending")
    drows, n_jc = _dispatch3()
    dispatch_avg = _pf.aggregate_dispatch(drows, n_jc)["by_name"] if drows else None
    po_intel = _po_intel()
    # Projection LIVE from CRM: replicates SP_SCBusinessPlan_GetDetailedReportJCWise
    # for the planning JC — Current = JC{n} WK1+WK2, Next1 = JC{n} Next1, Next2 = Next2.
    proj_rows = _try(lambda: _crm.business_plan_projection(acc_year, plan_jc), "projection")
    projection = _pf.projection_from_crm(proj_rows or [], drop_zero=False)
    _intransit_rows, _intransit_from = _po_intransit(s, today)
    # MSL safety-stock buffer per finished-product name — only VALID items (freq > 10 and
    # unique customers > 5) survive the MSL aggregate, so any name present here is a valid
    # buffer. Drives the MSL-aware Current-JC quantity: (Proj + Pending SOC + MSL) − On-hand.
    msl_map = None
    try:
        _mwin, _mrows = _msl_dispatch()
        _mbc, _mbd = _msl.activity_maps(_resolve_file("PLANNING_BOM_XLSX"))
        _mitems = _msl.aggregate(_mrows, _mwin, _mbc, _mbd, _business_map())
        msl_map = {_pf._squash(r["item_name"]): r["msl"] for r in _mitems}
    except Exception:
        msl_map = None
    rp = _pf.build_rm_planning(
        _resolve_file("BUSINESS_PLAN_XLSX"), _resolve_file("PLANNING_BOM_XLSX"),
        _resolve_file("STOCK_XLSX"), _resolve_file("PO_CSV"),
        accyear=acc_year, settings=s, stock_rows=stock_rows,
        pending_rows=pending_rows, soc_window=win, business_map=_business_map(),
        dispatch_avg=dispatch_avg, po_intel=po_intel, overrides=overrides,
        plan_mode=plan_mode, projection=projection, bom_overrides=bom_overrides,
        po_pending=_intransit_rows, mfg_pending_rows=mfg_pending_rows, pto_map=_pto_map(),
        msl_map=msl_map, division_map=_division_by_name(),
        plan_divisions=s.get("plan_divisions", ["Performance Chemicals"]))
    rp["planning_jc"] = plan_jc
    rp["planning_acc_year"] = acc_year
    _its = s.get("intransit_source", "crm") if _intransit_from else "file"
    rp["intransit"] = {"source": _its, "po_months": s.get("intransit_po_months", 4),
                       "blanket_po_qty": s.get("blanket_po_qty", 500000),
                       "from": str(_intransit_from) if _intransit_from else None}
    if isinstance(rp.get("summary"), dict):
        rp["summary"]["intransit_source"] = _its
        rp["summary"]["intransit_po_months"] = s.get("intransit_po_months", 4)
        rp["summary"]["blanket_po_qty"] = s.get("blanket_po_qty", 500000)
    rp["intransit_unmatched"] = _intransit_unmatched(rp) if _its == "crm" else []
    if _its == "crm" and isinstance(rp.get("summary"), dict):
        # "Items with PO pending" must match the download's PO In-transit + In-transit
        # Unmatched sheets: unique non-packing items in-transit within the RM orgs only
        # (not ports/depots), so the dashboard number reconciles with the workbook.
        _rm_orgs = {_pf._norm(o).upper() for o in (rp.get("rules") or {}).get("rm_source_orgs", [])}
        _pend = {lt.get("item_code") for lt in _intransit_lots_audit()
                 if lt.get("item_code") and not _pf._pack_code(lt.get("item_code"))
                 and (lt.get("in_transit") or 0) > 0
                 and (not _rm_orgs or _pf._norm(lt.get("org")).upper() in _rm_orgs)}
        rp["summary"]["po_pending_items"] = len(_pend)
    if pj.get("from"):
        rp["planning_jc_from"] = pj["from"]
        rp["planning_jc_to"] = pj["to"]
    rp["projection_source"] = "CRM SP_SCBusinessPlan_GetDetailedReportJCWise (replicated)"
    if not any(v.get("current") for v in projection.values()):
        rp["projection_jc_note"] = (
            f"No approved projection found in CRM for JC{plan_jc} ({acc_year}). "
            f"It may not be compiled yet — it is compiled on JC{plan_jc}'s "
            f"preceding 3rd-week Monday.")
    return rp


@lru_cache(maxsize=1)
def _rm_planning():
    return _build_rm()


@lru_cache(maxsize=1)
def _aged_rm():
    if not _live():
        return {"summary": {}, "producible": [], "recommended": [],
                "note": "Requires DATA_SOURCE=live."}
    s = _ps.load()
    days = int(s.get("aged_rm_days", 90))
    aged_rows = _try(lambda: _crm.stock_details_aged(days), "aged_stock")
    all_rows = _crm_stock()
    return _pf.build_aged_rm_plan(_resolve_file("PLANNING_BOM_XLSX"), aged_rows, all_rows,
                                  settings=s, aged_days=days, business_map=_business_map())


@lru_cache(maxsize=1)
def _proj_sales_live():
    if not _live():
        return {"items": [], "collector_items": [], "summary": {},
                "note": "Requires DATA_SOURCE=live."}
    s = _ps.load()
    drows, n_jc = _dispatch3()
    stock_rows = _crm_stock()
    today = _date.today()
    pj = _jc.planning_jc_entry(today) or _jc.current_jc_entry(today) or {}
    plan_jc = pj.get("jc") or _jc.current_jc(today)
    _fy = _jc.fiscal_year(today)
    acc_year = os.getenv("BP_ACCYEAR") or pj.get("fy") or f"{_fy}-{_fy + 1}"
    proj_rows = _try(lambda: _crm.business_plan_projection_rows(acc_year, plan_jc), "proj-rows") or []
    projection_rows = list(_pf.projection_rows_from_crm(proj_rows))
    rp = _pf.build_projection_vs_sales(None, drows, stock_rows,
                                       settings=s, business_map=_business_map(),
                                       accyear=acc_year, n_jc=n_jc,
                                       bom_path=_resolve_file("PLANNING_BOM_XLSX"),
                                       projection_rows=projection_rows)
    rp["planning_jc"] = plan_jc
    return rp


# ── projection accuracy: received projection vs actual production (RM_Consumption) ──
@lru_cache(maxsize=1)
def _consump_index():
    return _rmc.discover()


@lru_cache(maxsize=64)
def _proj_current_merged(acc_year: str, jcs: tuple, approved: bool = True):
    """Merged projection {NAME_UPPER: {name,current,segment2,segment3}} summing the
    'Current' (WK1+WK2) plan across the given JC numbers for an accounting year."""
    merged: dict = {}
    for j in jcs:
        rows = _try(lambda j=j: _crm.business_plan_projection(acc_year, j, approved_only=approved),
                    f"proj-jc{j}") or []
        for _k, v in _pf.projection_from_crm(rows, drop_zero=False).items():
            e = merged.setdefault(_k, {"name": v["name"], "current": 0.0,
                                       "segment2": v.get("segment2", ""),
                                       "segment3": v.get("segment3", "")})
            e["current"] += v.get("current", 0.0)
            if not e["segment2"]:
                e["segment2"] = v.get("segment2", "")
            if not e["segment3"]:
                e["segment3"] = v.get("segment3", "")
    return merged


@lru_cache(maxsize=32)
def _proj_accuracy(acc_year: str | None = None, jc: int | None = None, approved: bool = False):
    idx = _consump_index()
    if not idx:
        return {"items": [], "divisions": [], "products": [], "summary": {},
                "scope": {}, "note": "No RM_Consumption files found."}
    acc_year = acc_year or sorted(idx)[-1]      # newest accounting year by default
    slot = idx.get(acc_year) or {}
    if not slot:
        return {"items": [], "divisions": [], "products": [], "summary": {},
                "scope": {"acc_year": acc_year}, "note": f"No consumption files for {acc_year}."}
    file_jcs = sorted(j for j in slot if j > 0)
    has_full = 0 in slot
    if jc is None:                              # aggregate: whole accounting year
        used = list(slot.values())
        proj_jcs = tuple(file_jcs) if file_jcs else tuple(range(1, 14))
        label = "All JCs (year-to-date)"
    else:
        v = slot.get(jc)
        if v is None:
            return {"items": [], "divisions": [], "products": [], "summary": {},
                    "scope": {"acc_year": acc_year, "jc": jc},
                    "note": f"No consumption file for JC{jc} in {acc_year}."}
        used = [v]
        proj_jcs = (jc,) if jc > 0 else tuple(range(1, 14))
        label = f"JC{jc}" if jc > 0 else "Full year"
    production = _rmc.production_by_item([v["path"] for v in used])
    projection = _proj_current_merged(acc_year, proj_jcs, approved) if _live() else {}
    scope = {"acc_year": acc_year, "jc": jc, "label": label, "live": _live(),
             "approved_only": approved,
             "files": [v["fname"] for v in used],
             "proj_jcs": list(proj_jcs),
             "available_years": sorted(idx, reverse=True),
             "available_jcs": file_jcs, "has_full_year": has_full}
    rp = _pacc.build(projection, production, scope)
    if not _live():
        rp["note"] = ("Projection needs DATA_SOURCE=live — showing actual production only "
                      "(projected = 0).")
    return rp


@lru_cache(maxsize=1)
def _scorecard_live():
    rows = _po_rows()
    if not rows:
        return {"suppliers": [], "summary": {}, "note": "No PO receipts found."}
    return _pf.build_supplier_scorecard(None, settings=_ps.load(), rows=rows)


@lru_cache(maxsize=1)
def _ppv_live():
    rows = _po_rows()
    if not rows:
        return {"items": [], "jc_performance": [], "summary": {}, "note": "No PO receipts found."}
    return _pf.build_ppv(None, std_fy=_ps.load().get("ppv_std_fy", "2025-26"),
                         settings=_ps.load(), rows=rows)


@lru_cache(maxsize=1)
def _adhoc_inputs():
    """Post-freeze open SOC + current-JC projection + pending SOC + freeze info."""
    s = _ps.load()
    today = _date.today()
    freeze = _jc.active_freeze(today) or today.isoformat()
    cur = _jc.current_jc_entry(today) or {}
    pj = _jc.planning_jc_entry(today) or cur or {}
    plan_jc = pj.get("jc") or _jc.current_jc(today)
    _fy = _jc.fiscal_year(today)
    acc_year = os.getenv("BP_ACCYEAR") or pj.get("fy") or f"{_fy}-{_fy + 1}"
    soc_rows = _try(lambda: _crm.soc_detail(freeze), "soc") or []
    proj_rows = _try(lambda: _crm.business_plan_projection(acc_year, plan_jc), "projection") or []
    proj_raw = _pf.projection_from_crm(proj_rows, drop_zero=False)
    projection = {_pf._squash(v["name"]): v.get("current", 0.0) for v in proj_raw.values()}
    win = _jc.soc_window(today, int(s.get("soc_window_months", 0)))
    pend_rows = _try(lambda: _crm.despatch_pending(win[0], win[1]), "pending") or []
    pending: dict = {}
    for r in pend_rows:
        k = _pf._squash(r.get("ItemDesc"))
        if k:
            pending[k] = pending.get(k, 0.0) + _pf._num(r.get("PendingQty"))
    freeze_info = {"freeze_date": freeze, "jc_freeze": cur.get("freeze_date"),
                   "fy": cur.get("fy"), "jc": cur.get("jc"),
                   "jc_from": cur.get("from"), "jc_to": cur.get("to"),
                   "pending_from": win[0], "pending_to": win[1]}
    return soc_rows, projection, set(projection.keys()), pending, freeze_info


def _adhoc(plan_id=None):
    if not _live():
        return {"products": [], "consolidated_rm": [], "summary": {},
                "note": "Requires DATA_SOURCE=live."}
    s = _ps.load()
    soc, projection, pnames, pending, freeze = _adhoc_inputs()
    allocs = _mysql.get_plan_allocations(plan_id) if plan_id else []
    rp = _pf.build_adhoc_planning(soc, _resolve_file("PLANNING_BOM_XLSX"), _crm_stock(),
                                  projected_names=pnames, settings=s, business_map=_business_map(),
                                  projection=projection, pending=pending, allocations=allocs,
                                  freeze_info=freeze)
    rp["jc_plans"] = _mysql.list_jc_plans()
    rp["selected_plan_id"] = plan_id
    rp["mysql_ready"] = _mysql.status()["ready"]
    return rp


@lru_cache(maxsize=1)
def _pto_map():
    """{squash(item description) -> 'PTS' | 'PTO'} from the CRM PTO/PTS master.
    Drives shared-RM allocation priority (PTS served before PTO)."""
    m: dict = {}
    if not _live():
        return m
    for r in (_try(_crm.pto_pts, "pto_pts") or []):
        name = _pf._norm(r.get("Item_Name"))
        it = _pf._norm(r.get("Itemtype")).upper()
        if name and it in ("PTS", "PTO"):
            k = _pf._squash(name)
            # PTS wins if any code of the description is PTS
            if k and (k not in m or it == "PTS"):
                m[k] = it
    return m


@lru_cache(maxsize=1)
def _template_items():
    """FG items (Performance Chemicals, per Segment2/Segment3) from the item
    master — the LOV for the planning-input template."""
    rows = _try(_crm.pto_pts, "pto_pts") or []
    out, seen = [], set()
    for r in rows:
        name = _pf._norm(r.get("Item_Name"))
        s2 = _pf._norm(r.get("Segment2"))
        s3 = _pf._norm(r.get("Segment3"))
        if not name or s2.lower() == "raw material":
            continue
        k = (name, s2, s3)
        if k not in seen:
            seen.add(k)
            out.append({"name": name, "segment2": s2, "segment3": s3})
    return out


def _plan_rm_rows(rp):
    """RM allocation ledger rows (by material) tagged with activity —
    materials tagged manufacturing/repack_relabel/mixed, plus packing material."""
    mfg = {x["rm_code"] for x in rp.get("consolidated_rm_manufacturing", [])}
    rep = {x["rm_code"] for x in rp.get("consolidated_rm_repack", [])}

    def act(code):
        m, r = code in mfg, code in rep
        return ("manufacturing" if m and not r else "repack_relabel" if r and not m
                else "mixed" if m and r else "unclassified")
    rows = [{"rm_code": x["rm_code"], "rm_desc": x["rm_desc"], "allocated_qty": x.get("gross_total", 0.0),
             "activity": act(x["rm_code"])}
            for x in rp.get("consolidated_rm", []) if x.get("gross_total", 0.0) > 0]
    rows += [{"rm_code": x["rm_code"], "rm_desc": x["rm_desc"], "allocated_qty": x.get("gross_total", 0.0),
              "activity": "packing"}
             for x in rp.get("consolidated_rm_packing", []) if x.get("gross_total", 0.0) > 0]
    return rows


def _persist_plan(rp, ptype, note, excel_keys=None):
    """Save a built plan: JC_PLAN + RM_ALLOCATION_LEDGER + PLAN_FG_DEMAND (with each
    FG's bom_class + effective bom_variant). Returns the save dict."""
    _mysql.seed_jc_master(_jc.master_rows())
    cur = _jc.current_jc_entry(_date.today()) or {}
    plan_jc = rp.get("planning_jc") or cur.get("jc") or 0
    fy = rp.get("planning_acc_year") or cur.get("fy")
    excel_keys = excel_keys or set()
    demand = []
    for p in rp.get("products", []):
        pj = p.get("projection") or {}
        cq = pj.get("current_target", pj.get("current", 0.0)) or 0.0
        n1, n2 = pj.get("next1", 0.0) or 0.0, pj.get("next2", 0.0) or 0.0
        src = "EXCEL" if _pf._squash(p["name"]) in excel_keys else "CRM"
        demand.append({"item_name": p["name"], "current_jc": cq, "next_jc1": n1, "next_jc2": n2,
                       "source": src, "bom_class": p.get("bom_class", "none"),
                       "bom_variant": p.get("bom_variant"), "_bom": bool(p.get("has_bom"))})
    fg_qty = round(sum(d["current_jc"] for d in demand if d["_bom"]), 2)
    fg_count = sum(1 for d in demand if d["_bom"] and d["current_jc"] > 0)
    save = _mysql.save_jc_plan(fy, plan_jc, ptype, fg_qty, fg_count, _plan_rm_rows(rp), note=note)
    if save["ok"]:
        _mysql.save_plan_fg_demand(save["plan_id"], demand)
    return save


def _save_jc_plan(note: str = ""):
    """Persist the current JC RM planning as a JC_PLAN (+ RM_ALLOCATION_LEDGER)."""
    _mysql.seed_jc_master(_jc.master_rows())
    rp = _rm_planning()
    cur = _jc.current_jc_entry(_date.today()) or {}
    # the plan is built for the PLANNING JC (roll-forward), not the calendar JC
    plan_jc = rp.get("planning_jc") or cur.get("jc") or 0
    fy = rp.get("planning_acc_year") or cur.get("fy")
    fg_qty, fg_count = 0.0, 0
    for p in rp.get("products", []):
        cur_qty = (p.get("projection") or {}).get("current", 0.0)
        if p.get("has_bom") and cur_qty > 0:
            fg_qty += cur_qty
            fg_count += 1
    rm_rows = _plan_rm_rows(rp)
    return _mysql.save_jc_plan(fy, plan_jc, "JC", fg_qty, fg_count, rm_rows, note)


@lru_cache(maxsize=1)
def _vooki_fg_map():
    return _mysql.get_vooki_fg_map()


@lru_cache(maxsize=1)
def _added_fg_skus():
    return _mysql.get_added_fg_skus()


@lru_cache(maxsize=1)
def _vessel_rows():
    return _mysql.get_vessel_mapping()


@lru_cache(maxsize=1)
def _soc_schedule():
    return _try(_crm.soc_schedule, "soc_schedule") or [] if _live() else []


def _production_schedule(plan_id):
    if not _live():
        return {"jobs": [], "unscheduled": [], "summary": {}, "note": "Requires DATA_SOURCE=live."}
    meta = _mysql.get_jc_plan(plan_id)
    if not meta:
        return {"jobs": [], "unscheduled": [], "summary": {},
                "note": "Plan not found — save/generate a JC plan first (needs the plan-demand tables)."}
    jc_start = None
    for j in _jc.master_rows():
        if j["fy"] == meta.get("fy") and j["jc_number"] == meta.get("jc_number"):
            jc_start = j["start_date"]
            break
    demand = _mysql.get_plan_fg_demand(plan_id)
    bom_ov = {_pf._squash(d["item_name"]): d["bom_variant"] for d in demand if d.get("bom_variant")}
    rp = _sched.build_production_schedule(
        demand, jc_start, _soc_schedule(), _crm_stock(),
        _resolve_file("PLANNING_BOM_XLSX"), _po_intel(), _vessel_rows(),
        settings=_ps.load(), today_iso=_date.today().isoformat(), bom_overrides=bom_ov)
    rp["plan"] = {"plan_id": plan_id, "fy": meta.get("fy"), "jc_number": meta.get("jc_number"),
                  "plan_type": meta.get("plan_type"),
                  "plan_datetime": str(meta.get("plan_datetime")) if meta.get("plan_datetime") else ""}
    return rp


@lru_cache(maxsize=1)
def _vooki_division_items():
    if not _live():
        return []
    rows = _try(lambda: _crm.vooki_division_items(_ps.load().get("vooki_business", "Vooki Division")),
                "vooki_items") or []
    out = []
    for r in rows:
        code, desc = _pf._norm(r.get("ItemCode")), _pf._norm(r.get("ItemDesc"))
        if code:
            out.append({"code": code, "desc": desc})
    return out


@lru_cache(maxsize=1)
def _vooki_planning():
    if not _live():
        return {"products": [], "summary": {}, "note": "Requires DATA_SOURCE=live."}
    s = _ps.load()
    stock_rows = _crm_stock() or None
    return _pf.build_vooki_planning(
        _resolve_file("PLANNING_BOM_XLSX"), _resolve_file("VOOKI_MASTER_CSV"),
        stock_rows=stock_rows, po_path=_resolve_file("PO_CSV"),
        po_intel=_po_intel(), settings=s, business_map=_business_map(),
        fg_map=_vooki_fg_map(), extra_fg=_added_fg_skus())


def _live_cycle() -> str:
    try:
        return _get_data().get("cycle_period", "")
    except Exception:   # noqa: BLE001
        return ""


def _xlsx(data: bytes, name: str) -> StreamingResponse:
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _reset_live_caches():
    for f in (_business_map, _crm_stock, _stock_lots_audit, _dispatch3, _po_rows, _po_intel, _po_pending, _po_ingest,
              _rm_planning, _aged_rm, _aged_rm_report, _proj_sales_live, _proj_accuracy, _proj_current_merged, _consump_index,
              _scorecard_live, _ppv_live, _adhoc_inputs, _vooki_planning, _vooki_fg_map,
              _added_fg_skus, _vooki_division_items, _template_items,
              _vessel_rows, _soc_schedule):
        f.cache_clear()


# ── overview ──────────────────────────────────────────────────────────────────

@app.get("/api/overview")
def get_overview():
    data   = _get_data()
    cycle, lock_meta, _ = _get_cycle()
    dq_r   = _get_dq()
    bases  = _get_baselines()
    segm   = _get_segmentation()
    supply = _get_supply()
    confs  = db_state.load_confirmations(cycle)

    # ── validation classification pass ──────────────────────────────────────
    exc_summary: dict[str, int] = {}
    owner_bias_map = val.owner_bias(data, bases)
    for sid, sku in data["skus"].items():
        b   = bases[sid]
        cls = val.classify(
            sku["projection"], b["baseline"], sku["pending_soc"], sku["lms"],
            segm["abc_xyz"][sid]["cell"], b["intermittent"],
        )
        t = cls.get("type", "Auto-accept")
        exc_summary[t] = exc_summary.get(t, 0) + 1

    open_exc = sum(v for k, v in exc_summary.items() if k != "Auto-accept")

    # ── DQ summary ──────────────────────────────────────────────────────────
    dq_sum = dq_r.get("summary", {})
    dq_scores = dq_r.get("scores", {})

    # ── governance alerts ────────────────────────────────────────────────────
    val_r = val.build_validation(data, bases, segm)
    gov   = governance.build_governance(data, dq_r, val_r, supply, lock_meta is not None)
    alert_tiers = gov.get("alert_tiers", {"escalation": 0, "action": 0, "info": 0})
    gates        = gov.get("gates", [])

    # ── supply summary ───────────────────────────────────────────────────────
    sup_sum = supply.get("summary", {})
    rccp    = supply.get("rccp", [])

    # ── pipeline steps ───────────────────────────────────────────────────────
    pipeline = [
        "1. Portfolio review",
        "2. Demand validation",
        "3. Consensus lock",
        "4. Supply / RM plan",
        "5. RCCP",
        "6. S&OP review",
    ]

    # ── counts ───────────────────────────────────────────────────────────────
    families = {s["family"] for s in data["skus"].values()}

    return {
        # fields used by App.jsx header pills
        "cycle": {
            "cycle_period":    cycle,
            "step":            "Consensus" if lock_meta else "Review",
            "locked":          lock_meta is not None,
            "exceptions_open": open_exc,
            "confirmed":       len(confs),
            "total_skus":      len(data["skus"]),
            "dq_blocked":      len(dq_r.get("blocked_skus", [])),
        },
        # fields used by Overview.jsx
        "pipeline":          pipeline,
        "exception_summary": exc_summary,
        "owner_bias":        owner_bias_map,
        "supply_summary":    sup_sum,
        "rccp":              rccp,
        "counts":            {"skus": len(data["skus"]), "families": len(families)},
        "dq":                dq_sum,
        "dq_scores":         dq_scores,
        "alert_tiers":       alert_tiers,
        "gates":             gates,
        # meta
        "source":            data.get("_source", "synthetic"),
        "scope":             data.get("_scope"),
        "load_warnings":     data.get("_load_warnings", []),
    }


# ── data quality ──────────────────────────────────────────────────────────────

@app.get("/api/dq")
def get_dq():
    return _get_dq()


# ── validation ────────────────────────────────────────────────────────────────

@app.get("/api/validation")
def get_validation():
    data  = _get_data()
    bases = _get_baselines()
    segm  = _get_segmentation()
    cycle, lock_meta, _ = _get_cycle()
    confs = db_state.load_confirmations(cycle)
    result = val.build_validation(data, bases, segm)
    result["confirmations"] = confs
    result["locked"] = lock_meta is not None
    return result


class ConfirmBody(BaseModel):
    sku: str
    confirmed_qty: float
    note: str = ""


@app.post("/api/validation/confirm")
def confirm_sku(body: ConfirmBody):
    cycle, lock_meta, _ = _get_cycle()
    if lock_meta:
        raise HTTPException(400, "Cycle is locked — unlock first.")
    if body.sku not in _get_data()["skus"]:
        raise HTTPException(404, f"SKU {body.sku} not found.")
    conf = {"confirmed_qty": body.confirmed_qty, "note": body.note,
            "ts": datetime.utcnow().isoformat()}
    db_state.save_confirmation(cycle, body.sku, conf)
    db_state.append_audit(cycle, {"action": "confirm", "sku": body.sku,
                                  "qty": body.confirmed_qty, "note": body.note,
                                  "ts": conf["ts"]})
    return {"ok": True}


class LockBody(BaseModel):
    action: str


@app.post("/api/validation/lock")
def lock_cycle(body: LockBody):
    cycle, lock_meta, _ = _get_cycle()
    data  = _get_data()
    bases = _get_baselines()
    confs = db_state.load_confirmations(cycle)
    if body.action == "lock":
        if lock_meta:
            raise HTTPException(400, "Already locked.")
        consensus = {sid: confs[sid]["confirmed_qty"] if sid in confs
                     else bases[sid]["baseline"] for sid in data["skus"]}
        meta = {"locked_at": datetime.utcnow().isoformat(), "by": "planner"}
        db_state.set_lock(cycle, meta, consensus)
        db_state.append_audit(cycle, {"action": "lock", **meta})
    else:
        if not lock_meta:
            raise HTTPException(400, "Not locked.")
        db_state.clear_lock(cycle)
        db_state.append_audit(cycle, {"action": "unlock", "ts": datetime.utcnow().isoformat()})
    return {"ok": True}


# ── forecasting ───────────────────────────────────────────────────────────────

@app.get("/api/forecasting")
def get_forecasting():
    return fc.build_forecasting(_get_data(), _get_baselines())


# ── segmentation ──────────────────────────────────────────────────────────────

@app.get("/api/segmentation")
def get_segmentation():
    return _get_segmentation()


# ── supply ────────────────────────────────────────────────────────────────────

@app.get("/api/supply")
def get_supply():
    dq_r    = _get_dq()
    blocked = set(dq_r.get("blocked_skus", []))
    return sp.build_supply_plan(_get_data(), _get_baselines(), _get_segmentation(),
                                _get_consensus(), blocked)


# ── Supply & RM Plan (live filtration) ────────────────────────────────────────

@app.get("/api/rm-planning")
def get_rm_planning():
    return _rm_planning()


# Only these two divisions (ItemCategories.segment1) are shown on the MFG Org Stock page.
MFG_STOCK_DIVISIONS = ("Performance Chemicals", "NPD")


@lru_cache(maxsize=1)
def _mfg_stock():
    """On-hand stock at the MANUFACTURING orgs (org name contains 'MFG'/'Mfg'),
    aggregated by item × org, tagged with Division/Segment2/Segment3. Restricted to the
    Performance Chemicals & NPD divisions. Excluded sub-inventories and DM-water codes are
    dropped. For the MFG Org Stock page (filter/search/sort)."""
    if not _live():
        return {"rows": [], "orgs": [], "segments": [], "divisions": [], "summary": {}}
    s = _ps.load()
    excluded = {x.lower() for x in s.get("excluded_subinv", _pf.EXCLUDE_SUBINV)}
    dm = {_pf._norm(c).upper() for c in s.get("dm_water_codes", []) if c}
    allowed = set(MFG_STOCK_DIVISIONS)
    segmap = _item_segments()
    agg: dict = {}
    for r in (_try(_crm.stock_lots, "stock_lots") or []):
        org = _pf._norm(r.get("Organization"))
        if "mfg" not in org.lower():                 # MFG orgs only
            continue
        if _pf._norm(r.get("SubInv")).lower() in excluded:
            continue
        code = _pf._norm(r.get("ItemCode"))
        if not code or code.upper() in dm:           # drop DM water
            continue
        division, seg2, seg3 = segmap.get(code, ("", "", ""))
        if division not in allowed:                  # Performance Chemicals & NPD only
            continue
        qty = _pf._num(r.get("Qty"))
        if qty == 0:
            continue
        k = (code, org)
        a = agg.setdefault(k, {"item_code": code, "item_desc": _pf._norm(r.get("ItemDesc")),
                               "org": org, "org_code": _pf._norm(r.get("OrgCode")),
                               "division": division, "segment2": seg2, "segment3": seg3, "qty": 0.0,
                               "lots": 0, "age_days": 0})
        a["qty"] += qty
        a["lots"] += 1
        a["age_days"] = max(a["age_days"], int(_pf._num(r.get("AgeDays"))))
    rows = [{**a, "qty": round(a["qty"], 1)} for a in agg.values()]
    rows.sort(key=lambda x: -x["qty"])
    return {
        "rows": rows,
        "orgs": sorted({r["org"] for r in rows}),
        "segments": sorted({r["segment2"] for r in rows if r["segment2"]}),
        "divisions": sorted({r["division"] for r in rows if r["division"]}),
        "summary": {"items": len({r["item_code"] for r in rows}), "rows": len(rows),
                    "total_qty": round(sum(r["qty"] for r in rows), 1),
                    "orgs": len({r["org"] for r in rows}),
                    "stock_source": "CRM SPBiStockDetails (BiStockDetail)"},
    }


@app.get("/api/mfg-stock")
def get_mfg_stock():
    return _mfg_stock()


# ── MSL (Minimum Stock Level) ─────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _msl_dispatch():
    """Dispatch qty per item x collector over the latest 13 JCs (CRM FnDespatchDetails)."""
    window = _msl.jc_window()
    if not _live():
        return window, []
    return window, (_try(lambda: _crm.dispatch_by_jc(window), "msl_dispatch") or [])


@lru_cache(maxsize=1)
def _msl_data():
    window, rows = _msl_dispatch()
    by_code, by_desc = _msl.activity_maps(_resolve_file("PLANNING_BOM_XLSX"))
    s = _ps.load()
    stock_map = _msl.stock_by_code(_crm_stock() if _live() else [],
                                   s.get("warehouse_orgs", _pf.WAREHOUSE_ORGS),
                                   s.get("excluded_subinv", _pf.EXCLUDE_SUBINV))
    items = _msl.aggregate(rows, window, by_code, by_desc, _business_map(), stock_map)
    meta = _msl.window_meta(window)
    counts = {}
    for r in items:
        counts[r["activity"]] = counts.get(r["activity"], 0) + 1
    meta["summary"] = {"items": len(items), "by_activity": counts,
                       "total_msl": round(sum(r["msl"] for r in items), 1),
                       "total_onhand": round(sum(r.get("onhand_stock", 0) for r in items), 1),
                       "min_customers": _msl.MIN_CUSTOMERS, "min_freq": _msl.MIN_FREQ}
    return {"meta": meta, "rows": items}


@app.get("/api/msl")
def get_msl(activity: str | None = None, reference: str | None = None):
    """Live MSL (current 13-JC window) or a saved snapshot when ``reference`` is given.
    Optional ``activity`` filter: Manufacturing / Repack/Relabel / Trading."""
    if reference:
        snap = _msl.get_snapshot(reference)
        if not snap:
            raise HTTPException(status_code=404, detail=f"MSL snapshot '{reference}' not found")
        data = {"meta": snap.get("header", {}), "rows": snap.get("rows", [])}
    else:
        data = _msl_data()
    rows = data["rows"]
    if activity:
        rows = [r for r in rows if r.get("activity") == activity]
    return {"meta": data["meta"], "rows": rows, "storage": _msl.storage_info(),
            "activities": ["Manufacturing", "Repack/Relabel", "Trading", "Other"]}


@app.get("/api/msl/snapshots")
def list_msl_snapshots():
    return {"snapshots": _msl.list_snapshots(), "storage": _msl.storage_info()}


@app.post("/api/msl/save")
def save_msl(actor_code: str = "", actor_name: str = ""):
    data = _msl_data()
    res = _msl.save_snapshot(data["meta"], data["rows"], actor_name or actor_code)
    return {**res, "snapshots": _msl.list_snapshots()}


@app.get("/api/msl/export")
def export_msl(activity: str | None = None, reference: str | None = None):
    if reference:
        snap = _msl.get_snapshot(reference)
        if not snap:
            raise HTTPException(status_code=404, detail=f"MSL snapshot '{reference}' not found")
        data = {"meta": snap.get("header", {}), "rows": snap.get("rows", [])}
    else:
        data = _msl_data()
    return _xlsx(_pub.build_msl_workbook(data["meta"], data["rows"], activity),
                 f"MSL_{data['meta'].get('reference', 'current')}.xlsx")


@app.get("/api/rm-planning/export")
def export_rm_planning():
    return _xlsx(_pub.build_rm_planning_workbook(_rm_planning(), _live_cycle(), stock_lots=_stock_lots_audit(),
                                                 intransit_lots=_intransit_lots_audit()),
                 "Supply_RM_Planning.xlsx")


@app.get("/api/rm-planning/export-packing")
def export_packing_plan(plan_id: int | None = None):
    rp = (_rebuild_saved_plan(plan_id) if plan_id else None) or _rm_planning()
    return _xlsx(_pub.build_packing_workbook(rp, _live_cycle()), "Supply_Packing_Plan.xlsx")


class _ApplyBom(BaseModel):
    bom_overrides: dict = {}     # {product_name: "assembly|org|designator"}
    note: str = ""


@app.post("/api/rm-planning/apply")
def apply_bom_overrides(body: _ApplyBom):
    """Rebuild the RM plan forcing the user's chosen BOMs, then SAVE it so the
    override flows into the consolidated plan, Excel and Production Scheduling."""
    ov = {_pf._squash(k): v for k, v in (body.bom_overrides or {}).items() if v}
    if not ov:
        raise HTTPException(400, "No BOM overrides supplied.")
    rp = _build_rm(bom_overrides=ov)
    n = sum(1 for p in rp.get("products", []) if p.get("overridden"))
    save = _persist_plan(rp, "JC-BOMOVR", body.note or f"{n} BOM override(s)")
    rp["plan_id"], rp["plan_mode"], rp["excel_items"] = save.get("plan_id"), "bom_override", 0
    return {"plan_id": save.get("plan_id"), "overrides_applied": n,
            "mysql_ok": save["ok"], "mysql_error": save.get("error"), "plan": rp}


@app.get("/api/publish")
def publish_plan():
    return _xlsx(_pub.build_rm_planning_workbook(_rm_planning(), _live_cycle()), "SOP_Plan.xlsx")


# ── plan-input template (Segment2/Segment3 -> Excel with Item Description LOV) ──
@app.get("/api/supply/template-segments")
def template_segments():
    combos: dict = {}
    for it in _template_items():
        if it["segment2"]:
            combos.setdefault(it["segment2"], set()).add(it["segment3"])
    return {"segment1": "Performance Chemicals",
            "segments": [{"segment2": k, "segment3": sorted(x for x in v if x)}
                         for k, v in sorted(combos.items())]}


@app.get("/api/supply/template/download")
def template_download(segment2: str = "", segment3: str = ""):
    names = sorted({it["name"] for it in _template_items()
                    if (not segment2 or it["segment2"] == segment2)
                    and (not segment3 or it["segment3"] == segment3)})
    seg = "_".join(x for x in (segment2, segment3) if x).replace(" ", "_")[:40] or "All"
    return _xlsx(_pub.build_plan_template_workbook(names, segment2, segment3),
                 f"Plan_Template_{seg}.xlsx")


@app.post("/api/supply/plan/upload")
async def upload_plan(file: UploadFile | None = File(None), mode: str = Form("consolidate")):
    """Generate + save a JC plan. mode='crm' = Projection + Pending SOC only (no
    Excel); mode='consolidate' merges the Excel with CRM projection + pending SOC
    (Excel adds/overrides items); mode='excel_only' plans purely from the Excel.
    Saved to JC_PLAN + RM_ALLOCATION_LEDGER + PLAN_FG_DEMAND with a Plan ID + date."""
    plan_mode = mode if mode in ("crm", "consolidate", "excel_only") else "consolidate"
    overrides = []
    if plan_mode != "crm":
        if file is None:
            raise HTTPException(400, "Choose a filled template Excel for this mode.")
        overrides = _pf.parse_plan_template(await file.read())
        if not overrides:
            raise HTTPException(400, "No usable rows in the template — need Item Description + a JC quantity.")
    rp = _build_rm(overrides=overrides, plan_mode=plan_mode)
    ptype = {"crm": "JC", "consolidate": "JC-CONSOLIDATED", "excel_only": "JC-EXCEL"}[plan_mode]
    excel_keys = {_pf._squash(o["name"]) for o in overrides}
    save = _persist_plan(rp, ptype, f"{plan_mode} · {len(overrides)} Excel items", excel_keys)
    rp["plan_id"], rp["plan_mode"], rp["excel_items"] = save.get("plan_id"), plan_mode, len(overrides)
    return {"plan_id": save.get("plan_id"), "plan_mode": plan_mode, "excel_items": len(overrides),
            "mysql_ok": save["ok"], "mysql_error": save.get("error"), "plan": rp}


def _rebuild_saved_plan(plan_id):
    """Rebuild a saved plan's RM planning from PLAN_FG_DEMAND (Excel + BOM overrides
    + mode), so exports match the plan shown on screen. None if plan not found."""
    meta = _mysql.get_jc_plan(plan_id)
    if not meta:
        return None
    plan_mode = "excel_only" if meta.get("plan_type") == "JC-EXCEL" else "consolidate"
    demand = _mysql.get_plan_fg_demand(plan_id)
    overrides = [{"name": d["item_name"], "current": d["current_jc"],
                  "next1": d["next_jc1"], "next2": d["next_jc2"]}
                 for d in demand if d["source"] == "EXCEL"]
    bom_ov = {_pf._squash(d["item_name"]): d["bom_variant"] for d in demand if d.get("bom_variant")}
    return _build_rm(overrides=overrides, plan_mode=plan_mode, bom_overrides=bom_ov)


@app.get("/api/supply/plan/export")
def export_uploaded_plan(plan_id: int):
    """Export a saved uploaded plan (rebuilt from PLAN_FG_DEMAND overrides + mode),
    so the download matches the Excel/consolidated plan shown on screen."""
    rp = _rebuild_saved_plan(plan_id)
    if rp is None:
        raise HTTPException(404, "Plan not found (run the plan-demand migration and re-generate).")
    return _xlsx(_pub.build_rm_planning_workbook(rp, _live_cycle(), stock_lots=_stock_lots_audit(),
                                                 intransit_lots=_intransit_lots_audit()),
                 f"Supply_RM_Plan_{plan_id}.xlsx")


@app.get("/api/rm-planning/export-by-segment")
def export_rm_by_segment(plan_id: int | None = None):
    """A ZIP with one Excel file per Segment 2 (each split Manufacturing / Others),
    each product sheet followed by a per-collector projection sheet."""
    rp = (_rebuild_saved_plan(plan_id) if plan_id else None) or _rm_planning()
    # per (item x collector) projection so each sheet gets a 'Collector' breakdown
    _pr = []
    _ay, _pjc = rp.get("planning_acc_year"), rp.get("planning_jc")
    if _ay and _pjc:
        _raw = _try(lambda: _crm.business_plan_projection_rows(_ay, _pjc), "proj-rows-seg") or []
        _pr = list(_pf.projection_rows_from_crm(_raw))
    # per (item x collector) MFG SOC pending, for the MFG-SOC-by-collector sheets
    _soc = []
    _s = _ps.load()
    _win = rp.get("soc_window") or {}
    if _win.get("from") and _win.get("to"):
        _sraw = _try(lambda: _crm.despatch_pending_mfg_rows(_win["from"], _win["to"], _s.get("mfg_soc_orgs")),
                     "mfg-soc-rows") or []
        _soc = [{"name": _pf._norm(r.get("ItemDesc")), "collector": _pf._norm(r.get("Collector")) or "—",
                 "qty": _pf._num(r.get("PendingQty"))} for r in _sraw]
    data = _pub.build_rm_by_segment_zip(rp, _live_cycle(), stock_lots=_stock_lots_audit(),
                                        proj_rows=_pr, soc_rows=_soc)
    return StreamingResponse(
        iter([data]), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="Supply_RM_By_Segment.zip"'})


# ── adhoc planning (live SOC, post-freeze, JC-plan aware) ─────────────────────

@app.get("/api/adhoc-planning")
def get_adhoc_planning(plan_id: int | None = None):
    return _adhoc(plan_id)


@app.get("/api/adhoc-planning/export")
def export_adhoc_planning(plan_id: int | None = None):
    return _xlsx(_pub.build_adhoc_workbook(_adhoc(plan_id), _live_cycle()), "Adhoc_Planning.xlsx")


class _AdhocRun(BaseModel):
    plan_id: int | None = None


@app.post("/api/adhoc-planning/run")
def run_adhoc_planning(body: _AdhocRun):
    """Run adhoc evaluation and LOG each item to ADHOC_EVALUATION."""
    rp = _adhoc(body.plan_id)
    fz = rp.get("freeze", {})
    rows = [{"item_name": p["name"], "projected_qty": p.get("projected_qty", 0),
             "pending_soc_qty": p.get("pending_soc_qty", 0), "order_qty": p.get("soc_qty", 0),
             "adhoc_qty": p.get("adhoc_qty", 0), "status": p.get("status", "")}
            for p in rp.get("products", [])]
    res = _mysql.save_adhoc_evaluations(body.plan_id, fz.get("fy"), fz.get("jc"), rows)
    rp["logged"] = res
    return rp


# ── JC plan persistence (JC_PLAN + RM_ALLOCATION_LEDGER) ──────────────────────

class _SaveJcPlan(BaseModel):
    note: str = ""


@app.post("/api/jc-plan/save")
def save_jc_plan_api(body: _SaveJcPlan):
    res = _save_jc_plan(body.note)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Save failed")
    return {"ok": True, "plan_id": res["plan_id"]}


@app.get("/api/jc-plans")
def list_jc_plans_api():
    return {"ready": _mysql.status()["ready"], "plans": _mysql.list_jc_plans()}


# ── Production Job Scheduling (vessel-based, per chosen JC plan) ───────────────
def _default_plan_id(plans):
    """Latest plan that actually has FG demand (skip empty/test saves), else newest."""
    if not plans:
        return None
    return next((p["plan_id"] for p in plans if (p.get("planned_fg_qty") or 0) > 0),
                plans[0]["plan_id"])


@app.get("/api/production-schedule")
def get_production_schedule(plan_id: int | None = None):
    plans = _mysql.list_jc_plans()
    if not plan_id:
        plan_id = _default_plan_id(plans)
    rp = _production_schedule(plan_id) if plan_id else {
        "jobs": [], "unscheduled": [], "summary": {}, "note": "No JC plan saved yet."}
    rp["jc_plans"] = plans
    rp["selected_plan_id"] = plan_id
    rp["vessel_ready"] = bool(_vessel_rows())
    return rp


@app.get("/api/production-schedule/export")
def export_production_schedule(plan_id: int | None = None):
    plans = _mysql.list_jc_plans()
    if not plan_id:
        plan_id = _default_plan_id(plans)
    rp = _production_schedule(plan_id) if plan_id else {"jobs": [], "unscheduled": [], "summary": {}}
    return _xlsx(_pub.build_production_schedule_workbook(rp, _live_cycle()),
                 f"Production_Schedule_{plan_id or 'none'}.xlsx")


# ── item receipt schedule (W1..W4 receipt view) ───────────────────────────────

@app.get("/api/item-receipt-schedule")
def get_item_receipt_schedule(plan_id: int | None = None, region: str = "South"):
    """Item Receipt Schedule for a JC plan, across four JC windows (W1..W4).

    For each planned FG: warehouse-available date = manufacturing completion +
    the standard lead time; branch receipt date = that + the selected region's
    logistic lead time. Both dates are bucketed into W1..W4."""
    settings = _ps.load()
    leads = settings.get("receipt_logistic_leads", {})
    std = int(settings.get("receipt_std_lead_days", 3))
    plans = _mysql.list_jc_plans()
    if not plan_id:
        plan_id = _default_plan_id(plans)
    prod = _production_schedule(plan_id) if plan_id else {
        "jobs": [], "note": "No JC plan saved yet."}
    rp = _rsched.build_receipt_schedule(prod, _jc.horizon(), std, region, leads)
    rp["jc_plans"] = plans
    rp["selected_plan_id"] = plan_id
    rp["regions"] = leads
    rp["region_states"] = settings.get("receipt_region_states", {})
    if prod.get("note"):
        rp["note"] = prod["note"]
    return rp


# ── aged RM (live) ────────────────────────────────────────────────────────────

@app.get("/api/aged-rm")
@app.get("/api/aged-rm-plan")
def get_aged_rm():
    return _aged_rm()


@app.get("/api/aged-rm/export")
@app.get("/api/aged-rm-plan/export")
def export_aged_rm():
    return _xlsx(_pub.build_aged_rm_workbook(_aged_rm(), _live_cycle()), "Aged_RM_Plan.xlsx")


@lru_cache(maxsize=1)
def _aged_rm_report():
    """Aged-RM excess analysis: aged qty/value vs last-3-JC consumption, last-3-JC sales
    requirement and projection requirement, per aged raw material."""
    if not _live():
        return {"rows": [], "jc_numbers": [], "summary": {}, "note": "Requires DATA_SOURCE=live."}
    s = _ps.load()
    days = int(s.get("aged_rm_days", 90))
    aged_rows = _try(lambda: _crm.stock_details_aged(days), "aged_stock")
    rp = _rm_planning()
    acc_year = rp.get("planning_acc_year")
    plan_jc = int(rp.get("planning_jc") or 0)
    jc_nums = [j for j in (plan_jc - 3, plan_jc - 2, plan_jc - 1) if j >= 1] or [1, 2, 3]
    cons = _rmc.rm_consumed_by_jc(acc_year, jc_nums) if acc_year else [(j, {}) for j in jc_nums]
    return _pf.build_aged_rm_report(rp, aged_rows, cons, jc_nums,
                                    _resolve_file("PLANNING_BOM_XLSX"),
                                    business_map=_business_map(), settings=s,
                                    segment_map=_item_segments())


@app.get("/api/aged-rm/report")
def get_aged_rm_report():
    return _aged_rm_report()


@app.get("/api/aged-rm/report-export")
def export_aged_rm_report():
    return _xlsx(_pub.build_aged_rm_report_workbook(_aged_rm_report(), _live_cycle()),
                 "Report_Aged_RM.xlsx")


# ── projection vs sales (live) ────────────────────────────────────────────────

@app.get("/api/projection-sales")
@app.get("/api/projection-vs-sales")
def get_projection_sales():
    return _proj_sales_live()


@app.get("/api/projection-sales/export")
@app.get("/api/projection-vs-sales/export")
def export_projection_sales():
    return _xlsx(_pub.build_projection_sales_workbook(_proj_sales_live(), _live_cycle()),
                 "Projection_vs_Sales.xlsx")


# ── projection accuracy (projection vs actual production) ─────────────────────

@app.get("/api/projection-accuracy/meta")
def projection_accuracy_meta():
    idx = _consump_index()
    return {"years": [{"acc_year": y, "jcs": sorted(j for j in idx[y] if j > 0),
                       "has_full": 0 in idx[y]} for y in sorted(idx, reverse=True)]}


@app.get("/api/projection-accuracy")
def get_projection_accuracy(acc_year: str | None = None, jc: int | None = None,
                            approved: bool = False):
    return _proj_accuracy(acc_year, jc, approved)


@app.get("/api/projection-accuracy/export")
def export_projection_accuracy(acc_year: str | None = None, jc: int | None = None,
                               approved: bool = False):
    rp = _proj_accuracy(acc_year, jc, approved)
    sc = rp.get("scope", {})
    fn = f"Projection_Accuracy_{sc.get('acc_year', '')}_{sc.get('label', '').replace(' ', '')}.xlsx"
    return _xlsx(_pub.build_projection_accuracy_workbook(rp, _live_cycle()), fn)


# ── supplier scorecard (live PO) ──────────────────────────────────────────────

@app.get("/api/supplier-scorecard")
def get_supplier_scorecard():
    return _scorecard_live()


@app.get("/api/supplier-scorecard/export")
def export_supplier_scorecard():
    return _xlsx(_pub.build_supplier_scorecard_workbook(_scorecard_live(), _live_cycle()),
                 "Supplier_Scorecard.xlsx")


# ── PPV (live PO) ─────────────────────────────────────────────────────────────

@app.get("/api/ppv")
def get_ppv():
    return _ppv_live()


@app.get("/api/ppv/export")
def export_ppv():
    return _xlsx(_pub.build_ppv_workbook(_ppv_live(), _live_cycle()), "PPV_Scorecard.xlsx")


# ── Vooki planning (user-input FG quantities) ──────────────────────────────────
@app.get("/api/vooki-planning")
def get_vooki_planning():
    return _vooki_planning()


class _VookiQty(BaseModel):
    quantities: dict[str, float] = {}
    product: str | None = None


@app.post("/api/vooki-planning/export")
def export_vooki_planning(body: _VookiQty):
    name = "Vooki_Planning.xlsx"
    if body.product:
        safe = "".join(ch if (ch.isalnum() or ch in " -_") else "" for ch in body.product)[:40].strip()
        name = f"Vooki_{safe.replace(' ', '_') or 'FG'}.xlsx"
    return _xlsx(_pub.build_vooki_workbook(_vooki_planning(), body.quantities, _live_cycle(),
                                           product=body.product,
                                           stock_rows=(_crm_stock() if _live() else None),
                                           intransit_lots=(_intransit_lots_audit() if _live() else None)), name)


# ── Admin: Vooki FG name -> SKU mapping (MySQL store) ──────────────────────────
@app.get("/api/vooki-fg-map")
def get_vooki_fg_map_api():
    st = _mysql.status()
    vp = _vooki_planning()
    mapping = _mysql.get_vooki_fg_map()   # live sku_code -> product_name (admin view)
    return {
        "ready": st["ready"], "error": st["error"],
        "products": [p["name"] for p in vp.get("products", [])],
        "skus": [{**sku, "product": mapping.get(sku["code"], "")}
                 for sku in vp.get("fg_skus", [])],
        "mapped": len(mapping),
        "setup_hint": _mysql.SETUP_HINT,
    }


class _FgMapRow(BaseModel):
    sku_code: str
    product_name: str = ""


@app.post("/api/vooki-fg-map")
def set_vooki_fg_map_api(body: _FgMapRow):
    res = _mysql.set_vooki_fg_map(body.sku_code, body.product_name)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Save failed")
    _vooki_fg_map.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True}


class _FgMapBulk(BaseModel):
    rows: list[_FgMapRow] = []


@app.post("/api/vooki-fg-map/bulk")
def bulk_vooki_fg_map_api(body: _FgMapBulk):
    res = _mysql.bulk_set_vooki_fg_map([r.model_dump() for r in body.rows])
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Save failed")
    _vooki_fg_map.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True, "written": res["written"]}


# ── Admin: add new Vooki FG SKUs (loaded from CRM Vooki Division items) ─────────
@app.get("/api/vooki-fg-skus")
def get_vooki_fg_skus_api():
    st = _mysql.status()
    added = _added_fg_skus()
    added_codes = {a["sku_code"] for a in added}
    candidates = [{**c, "added": c["code"] in added_codes} for c in _vooki_division_items()]
    return {"ready": st["ready"], "error": st["error"], "setup_hint": _mysql.SETUP_HINT,
            "added": added, "candidates": candidates}


class _FgSkuRow(BaseModel):
    sku_code: str
    item_desc: str = ""


@app.post("/api/vooki-fg-skus")
def add_vooki_fg_sku_api(body: _FgSkuRow):
    res = _mysql.add_fg_sku(body.sku_code, body.item_desc)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Add failed")
    _added_fg_skus.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True}


@app.post("/api/vooki-fg-skus/remove")
def remove_vooki_fg_sku_api(body: _FgSkuRow):
    res = _mysql.remove_fg_sku(body.sku_code)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Remove failed")
    _added_fg_skus.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True}


# ── demand-side compatibility (Validation / Analytics original pages) ──────────
REASON_CODES = [
    "Confirmed firm deal / tender awarded", "New customer or new market entry",
    "Promotion or campaign", "Price-driven pull-forward / pre-buy",
    "Lost business / churn", "Competitive pressure", "Regulatory or seasonal shift",
    "Correction of prior double-count", "Other (free text)",
]


@app.get("/api/confirmations")
def get_confirmations():
    data, bases, segm = _get_data(), _get_baselines(), _get_segmentation()
    cycle, lock_meta, _ = _get_cycle()
    confs = db_state.load_confirmations(cycle)
    rows = []
    for r in val.build_validation(data, bases, segm)["rows"]:
        sku = r["sku"]
        cand = r.get("candidate") or 0.0
        saved = confs.get(sku)
        if saved:
            conf = {"quantity": saved.get("confirmed_qty", saved.get("quantity", cand)),
                    "reason_code": saved.get("reason_code"), "note": saved.get("note"),
                    "status": "confirmed", "owner": r.get("owner")}
        else:
            conf = {"quantity": cand, "reason_code": None, "note": None,
                    "status": "auto-accepted" if r.get("within_band") else "open",
                    "owner": r.get("owner")}
        rows.append({**r, "candidate": cand, "confirmation": conf})
    return {"cycle_period": cycle, "rows": rows, "reason_codes": REASON_CODES,
            "locked": lock_meta is not None}


class _ConfirmBody(BaseModel):
    quantity: float
    reason_code: str | None = None
    note: str | None = None
    actor: str = "Sales"


@app.post("/api/confirmations/{sku}")
def post_confirmation(sku: str, body: _ConfirmBody):
    cycle, lock_meta, _ = _get_cycle()
    if lock_meta:
        raise HTTPException(400, "Consensus is locked; unlock first.")
    if sku not in _get_data()["skus"]:
        raise HTTPException(404, "Unknown SKU")
    conf = {"confirmed_qty": body.quantity, "quantity": body.quantity,
            "reason_code": body.reason_code, "note": body.note, "status": "confirmed",
            "ts": datetime.utcnow().isoformat()}
    db_state.save_confirmation(cycle, sku, conf)
    db_state.append_audit(cycle, {"action": "confirm", "sku": sku, "qty": body.quantity, "ts": conf["ts"]})
    return {"ok": True, "confirmation": conf}


class _ActorBody(BaseModel):
    actor: str = "Demand Planner"


@app.post("/api/consensus/lock")
def post_consensus_lock(body: _ActorBody):
    cycle, _, _ = _get_cycle()
    data, bases, segm = _get_data(), _get_baselines(), _get_segmentation()
    confs = db_state.load_confirmations(cycle)
    consensus = {}
    for r in val.build_validation(data, bases, segm)["rows"]:
        sku, cand = r["sku"], (r.get("candidate") or 0.0)
        saved = confs.get(sku)
        consensus[sku] = saved.get("confirmed_qty", cand) if saved else cand
    meta = {"locked_at": datetime.utcnow().isoformat(), "by": body.actor}
    db_state.set_lock(cycle, meta, consensus)
    db_state.append_audit(cycle, {"action": "lock", **meta})
    return {"ok": True, "lock_meta": meta}


@app.post("/api/consensus/unlock")
def post_consensus_unlock(body: _ActorBody):
    cycle, _, _ = _get_cycle()
    db_state.clear_lock(cycle)
    db_state.append_audit(cycle, {"action": "unlock", "ts": datetime.utcnow().isoformat()})
    return {"ok": True}


@app.get("/api/skus/{sku}/history")
def get_sku_history(sku: str):
    data = _get_data()
    if sku not in data["skus"]:
        raise HTTPException(404, "Unknown SKU")
    s = data["skus"][sku]
    return {"sku": sku, "name": s["name"], "family": s["family"],
            "history": data["history"][sku], "baseline": _get_baselines()[sku],
            "projection": s.get("projection"), "pending_soc": s.get("pending_soc"),
            "lms": s.get("lms"), "cycle_period": data["cycle_period"]}


class _WhatIfBody(BaseModel):
    demand_surge_pct: float = 0.0
    family: str | None = None
    supplier_outage: str | None = None
    capacity_loss_pct: float = 0.0


@app.post("/api/what-if")
def post_what_if(body: _WhatIfBody):
    fn = getattr(analytics, "what_if", None)
    if fn is None:
        return {"note": "What-if scenario engine not available in this build.",
                "scenario": body.model_dump()}
    try:
        return fn(_get_data(), _get_baselines(), _get_segmentation(),
                  _get_consensus(), body.model_dump(), set())
    except Exception as e:   # noqa: BLE001
        return {"note": f"What-if unavailable: {type(e).__name__}", "scenario": body.model_dump()}


# ── JC plan ──────────────────────────────────────────────────────────────────

@app.get("/api/jc-plan")
def get_jc_plan():
    return build_jc_plan(_get_data(), _get_baselines(), _get_segmentation())


# ── analytics ────────────────────────────────────────────────────────────────

@app.get("/api/analytics")
def get_analytics():
    return analytics.build_analytics(
        _get_data(), _get_baselines(), _get_segmentation(), _get_supply())


# ── KPIs ─────────────────────────────────────────────────────────────────────

@app.get("/api/kpis")
def get_kpis():
    data   = _get_data()
    bases  = _get_baselines()
    segm   = _get_segmentation()
    supply = _get_supply()
    val_r  = val.build_validation(data, bases, segm)
    return kpis.build_kpis(data, bases, segm, supply, val_r)


# ── governance ───────────────────────────────────────────────────────────────

@app.get("/api/governance")
def get_governance():
    data   = _get_data()
    bases  = _get_baselines()
    segm   = _get_segmentation()
    supply = _get_supply()
    dq_r   = _get_dq()
    _, lock_meta, _ = _get_cycle()
    val_r  = val.build_validation(data, bases, segm)
    return governance.build_governance(data, dq_r, val_r, supply, lock_meta is not None)


# ── planning settings ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _all_orgs():
    """Distinct organisation names present in live CRM stock — for the Planning
    Setting org pickers (add/remove org)."""
    if not _live():
        return []
    orgs = {_pf._norm(r.get("Organization")) for r in (_crm_stock() or [])}
    return sorted(o for o in orgs if o)


@app.get("/api/orgs")
def get_orgs():
    return {"orgs": _all_orgs()}


@app.get("/api/planning-settings")
def get_planning_settings():
    return {"settings": _ps.load(), "defaults": _ps.DEFAULTS}


@app.post("/api/planning-settings")
def save_planning_settings(updates: dict):
    merged = _ps.save(updates)
    _reset_live_caches()
    return {"ok": True, "settings": merged}


# ── audit ─────────────────────────────────────────────────────────────────────

@app.get("/api/audit")
def get_audit():
    cycle, _, _ = _get_cycle()
    return {"cycle": cycle, "entries": db_state.load_audit(cycle)}


# ── reset ─────────────────────────────────────────────────────────────────────

@app.post("/api/reset")
def reset():
    cycle, _, _ = _get_cycle()
    db_state.clear_cycle(cycle)
    _get_data.cache_clear()
    _get_baselines.cache_clear()
    _get_segmentation.cache_clear()
    _get_dq.cache_clear()
    _get_supply.cache_clear()
    _reset_live_caches()
    return {"ok": True}


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "source": _get_data().get("_source", "synthetic")}


@app.get("/api/health/db")
def health_db():
    from .integration.db import test_connection
    return test_connection()


# ── Excel export ──────────────────────────────────────────────────────────────

def _excel_response(result: dict, sheet_name: str) -> StreamingResponse:
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(500, "openpyxl not installed.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Key", "Value"])
    for k, v in result.get("summary", {}).items():
        ws.append([str(k), "" if v is None else str(v)])

    for key in ("products", "consolidated_rm", "items", "suppliers", "jc_performance"):
        rows = result.get(key, [])
        if not rows:
            continue
        ws2 = wb.create_sheet(key.replace("_", " ").title()[:31])
        flat = [{k: v for k, v in r.items() if not isinstance(v, (list, dict))} for r in rows]
        if flat:
            ws2.append(list(flat[0].keys()))
            for r in flat:
                ws2.append(["" if v is None else str(v) for v in r.values()])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{sheet_name}.xlsx"'},
    )


# ── background pre-warm ────────────────────────────────────────────────────────
# The live-CRM reads (dataset + RM filtration + PO files) take ~30-120s the first
# time. Warm the memoised caches in a background thread at startup so the pages
# load instantly once they're ready, instead of blocking on first navigation.

def _prewarm():
    import time
    time.sleep(1)
    steps = [
        (_get_data, "dataset"), (_get_baselines, "baselines"),
        (_get_segmentation, "segmentation"), (_get_dq, "dq"),
        (_business_map, "business-map"), (_rm_planning, "rm-planning"),
        (_aged_rm, "aged-rm"), (_proj_sales_live, "projection-vs-sales"),
        (_scorecard_live, "supplier-scorecard"), (_ppv_live, "ppv"), (_adhoc_inputs, "adhoc"),
    ]
    for fn, label in steps:
        t0 = time.time()
        try:
            fn()
            print(f"[prewarm] {label} ready ({time.time() - t0:.0f}s)")
        except Exception as e:   # noqa: BLE001
            print(f"[prewarm] {label} failed: {type(e).__name__}: {str(e).splitlines()[0][:140]}")
    print("[prewarm] all caches warm")


if os.getenv("PREWARM", "1") != "0":
    import threading
    threading.Thread(target=_prewarm, daemon=True, name="prewarm").start()
