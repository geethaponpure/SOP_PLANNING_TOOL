"""Production Job Scheduling + Item Receipt Schedule."""
from ._deps import *

router = APIRouter()


@router.get("/api/production-schedule")
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


@router.get("/api/production-schedule/export")
def export_production_schedule(plan_id: int | None = None):
    plans = _mysql.list_jc_plans()
    if not plan_id:
        plan_id = _default_plan_id(plans)
    rp = _production_schedule(plan_id) if plan_id else {"jobs": [], "unscheduled": [], "summary": {}}
    return _xlsx(_pub.build_production_schedule_workbook(rp, _live_cycle()),
                 f"Production_Schedule_{plan_id or 'none'}.xlsx")


@router.get("/api/item-receipt-schedule")
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
