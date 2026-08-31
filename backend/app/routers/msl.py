"""MSL (Minimum Stock Level) page + snapshots."""
from ._deps import *

router = APIRouter()


@router.get("/api/msl")
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


@router.get("/api/msl/snapshots")
def list_msl_snapshots():
    return {"snapshots": _msl.list_snapshots(), "storage": _msl.storage_info()}


@router.post("/api/msl/save")
def save_msl(actor_code: str = "", actor_name: str = ""):
    data = _msl_data()
    res = _msl.save_snapshot(data["meta"], data["rows"], actor_name or actor_code)
    return {**res, "snapshots": _msl.list_snapshots()}


@router.get("/api/msl/export")
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
