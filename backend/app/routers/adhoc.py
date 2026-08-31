"""Adhoc planning (live SOC, post-freeze, JC-plan aware)."""
from ._deps import *

router = APIRouter()


@router.get("/api/adhoc-planning")
def get_adhoc_planning(plan_id: int | None = None):
    return _adhoc(plan_id)


@router.get("/api/adhoc-planning/export")
def export_adhoc_planning(plan_id: int | None = None):
    return _xlsx(_pub.build_adhoc_workbook(_adhoc(plan_id), _live_cycle()), "Adhoc_Planning.xlsx")


@router.post("/api/adhoc-planning/run")
def run_adhoc_planning(body: AdhocRun):
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
