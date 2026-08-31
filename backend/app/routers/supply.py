"""Supply & RM planning: the plan, its exports, BOM overrides, plan upload/save."""
from ._deps import *

router = APIRouter()


@router.get("/api/supply")
def get_supply():
    dq_r    = _get_dq()
    blocked = set(dq_r.get("blocked_skus", []))
    return sp.build_supply_plan(_get_data(), _get_baselines(), _get_segmentation(),
                                _get_consensus(), blocked)


@router.get("/api/rm-planning")
def get_rm_planning():
    return _rm_planning()


@router.get("/api/rm-planning/export")
def export_rm_planning():
    return _xlsx(_pub.build_rm_planning_workbook(_rm_planning(), _live_cycle(), stock_lots=_stock_lots_audit(),
                                                 intransit_lots=_intransit_lots_audit()),
                 "Supply_RM_Planning.xlsx")


@router.get("/api/rm-planning/export-packing")
def export_packing_plan(plan_id: int | None = None):
    rp = (_rebuild_saved_plan(plan_id) if plan_id else None) or _rm_planning()
    return _xlsx(_pub.build_packing_workbook(rp, _live_cycle()), "Supply_Packing_Plan.xlsx")


@router.post("/api/rm-planning/apply")
def apply_bom_overrides(body: ApplyBom):
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


@router.get("/api/publish")
def publish_plan():
    return _xlsx(_pub.build_rm_planning_workbook(_rm_planning(), _live_cycle()), "SOP_Plan.xlsx")


@router.get("/api/supply/template-segments")
def template_segments():
    combos: dict = {}
    for it in _template_items():
        if it["segment2"]:
            combos.setdefault(it["segment2"], set()).add(it["segment3"])
    return {"segment1": "Performance Chemicals",
            "segments": [{"segment2": k, "segment3": sorted(x for x in v if x)}
                         for k, v in sorted(combos.items())]}


@router.get("/api/supply/template/download")
def template_download(segment2: str = "", segment3: str = ""):
    names = sorted({it["name"] for it in _template_items()
                    if (not segment2 or it["segment2"] == segment2)
                    and (not segment3 or it["segment3"] == segment3)})
    seg = "_".join(x for x in (segment2, segment3) if x).replace(" ", "_")[:40] or "All"
    return _xlsx(_pub.build_plan_template_workbook(names, segment2, segment3),
                 f"Plan_Template_{seg}.xlsx")


@router.post("/api/supply/plan/upload")
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


@router.get("/api/supply/plan/export")
def export_uploaded_plan(plan_id: int):
    """Export a saved uploaded plan (rebuilt from PLAN_FG_DEMAND overrides + mode),
    so the download matches the Excel/consolidated plan shown on screen."""
    rp = _rebuild_saved_plan(plan_id)
    if rp is None:
        raise HTTPException(404, "Plan not found (run the plan-demand migration and re-generate).")
    return _xlsx(_pub.build_rm_planning_workbook(rp, _live_cycle(), stock_lots=_stock_lots_audit(),
                                                 intransit_lots=_intransit_lots_audit()),
                 f"Supply_RM_Plan_{plan_id}.xlsx")


@router.get("/api/rm-planning/export-by-segment")
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


@router.post("/api/jc-plan/save")
def save_jc_plan_api(body: SaveJcPlan):
    res = _save_jc_plan(body.note)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Save failed")
    return {"ok": True, "plan_id": res["plan_id"]}


@router.get("/api/jc-plans")
def list_jc_plans_api():
    return {"ready": _mysql.status()["ready"], "plans": _mysql.list_jc_plans()}
