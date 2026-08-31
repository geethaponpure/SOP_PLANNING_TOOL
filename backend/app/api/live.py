"""Live-CRM / file loaders and plan builders.

Everything here is heavily interdependent (a plan build pulls a dozen cached
sources), so the whole live side lives in one module to keep the call graph flat
and free of circular imports. Loaders are memoised; ``_reset_live_caches`` clears
them when the admin settings or PO files change.
"""
from __future__ import annotations

import os
from datetime import date as _date
from functools import lru_cache

from ..integration import planning_filter as _pf
from ..integration import planning_settings as _ps
from ..integration import crm_sources as _crm
from ..integration import jc_calendar as _jc
from ..integration import mysql_db as _mysql
from ..integration import scheduling as _sched
from ..integration import rm_consumption as _rmc
from ..integration import projection_accuracy as _pacc
from ..integration import msl as _msl
from ..integration import staging          # Phase 1: read stock/segments from MySQL, not CRM
from ..integration.adapter import _resolve_file, _resolve_po_files, resolve_latest_po_register
from .. import publish as _pub
from .common import _live, _try, _months_ago


@lru_cache(maxsize=1)
def _business_map():
    m: dict = {}
    if not _live():
        return m
    rows = staging.read_item_business()
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
    for r in staging.read_item_segments():
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
    for r in staging.read_item_segments():
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
    return staging.read_stock_details()


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
    for r in staging.read_stock_lots():
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
    rows = staging.read_intransit()
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
    return staging.read_dispatch("jc3", len(jcs)), len(jcs)


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
    pending_rows = staging.read_soc_pending("all")
    mfg_pending_rows = staging.read_soc_pending("mfg")
    drows, n_jc = _dispatch3()
    dispatch_avg = _pf.aggregate_dispatch(drows, n_jc)["by_name"] if drows else None
    po_intel = _po_intel()
    # Projection LIVE from CRM: replicates SP_SCBusinessPlan_GetDetailedReportJCWise
    # for the planning JC — Current = JC{n} WK1+WK2, Next1 = JC{n} Next1, Next2 = Next2.
    proj_rows = staging.read_projection(acc_year, plan_jc)
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
    aged_rows = staging.read_stock_aged()
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
    soc_rows = staging.read_soc_detail()
    proj_rows = staging.read_projection(acc_year, plan_jc)
    proj_raw = _pf.projection_from_crm(proj_rows, drop_zero=False)
    projection = {_pf._squash(v["name"]): v.get("current", 0.0) for v in proj_raw.values()}
    win = _jc.soc_window(today, int(s.get("soc_window_months", 0)))
    pend_rows = staging.read_soc_pending("all")
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
    for r in staging.read_pto_pts():
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
    rows = staging.read_pto_pts()
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
    return staging.read_soc_schedule() if _live() else []


def _default_plan_id(plans):
    """Latest plan that actually has FG demand (skip empty/test saves), else newest."""
    if not plans:
        return None
    return next((p["plan_id"] for p in plans if (p.get("planned_fg_qty") or 0) > 0),
                plans[0]["plan_id"])


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
    rows = staging.read_vooki_items()
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
    for r in staging.read_stock_lots():
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


# ── MSL (Minimum Stock Level) ─────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _msl_dispatch():
    """Dispatch qty per item x collector over the latest 13 JCs (CRM FnDespatchDetails)."""
    window = _msl.jc_window()
    if not _live():
        return window, []
    return window, staging.read_dispatch("jc13", len(window))


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


@lru_cache(maxsize=1)
def _aged_rm_report():
    """Aged-RM excess analysis: aged qty/value vs last-3-JC consumption, last-3-JC sales
    requirement and projection requirement, per aged raw material."""
    if not _live():
        return {"rows": [], "jc_numbers": [], "summary": {}, "note": "Requires DATA_SOURCE=live."}
    s = _ps.load()
    days = int(s.get("aged_rm_days", 90))
    aged_rows = staging.read_stock_aged()
    rp = _rm_planning()
    acc_year = rp.get("planning_acc_year")
    plan_jc = int(rp.get("planning_jc") or 0)
    jc_nums = [j for j in (plan_jc - 3, plan_jc - 2, plan_jc - 1) if j >= 1] or [1, 2, 3]
    cons = _rmc.rm_consumed_by_jc(acc_year, jc_nums) if acc_year else [(j, {}) for j in jc_nums]
    return _pf.build_aged_rm_report(rp, aged_rows, cons, jc_nums,
                                    _resolve_file("PLANNING_BOM_XLSX"),
                                    business_map=_business_map(), settings=s,
                                    segment_map=_item_segments())


@lru_cache(maxsize=1)
def _all_orgs():
    """Distinct organisation names present in live CRM stock — for the Planning
    Setting org pickers (add/remove org)."""
    if not _live():
        return []
    orgs = {_pf._norm(r.get("Organization")) for r in (_crm_stock() or [])}
    return sorted(o for o in orgs if o)


def _reset_live_caches():
    for f in (_business_map, _crm_stock, _stock_lots_audit, _dispatch3, _po_rows, _po_intel, _po_pending, _po_ingest,
              _rm_planning, _aged_rm, _aged_rm_report, _proj_sales_live, _proj_accuracy, _proj_current_merged, _consump_index,
              _scorecard_live, _ppv_live, _adhoc_inputs, _vooki_planning, _vooki_fg_map,
              _added_fg_skus, _vooki_division_items, _template_items,
              _vessel_rows, _soc_schedule):
        f.cache_clear()


__all__ = [n for n, v in list(globals().items())
           if callable(v) and getattr(v, "__module__", None) == __name__ and not n.startswith("__")]
__all__ += ["MFG_STOCK_DIVISIONS"]
