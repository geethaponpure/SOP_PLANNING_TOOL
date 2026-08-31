"""Admin + system: org list, planning settings, cycle reset, health checks."""
from ._deps import *

router = APIRouter()


@router.get("/api/orgs")
def get_orgs():
    return {"orgs": _all_orgs()}


@router.get("/api/planning-settings")
def get_planning_settings():
    return {"settings": _ps.load(), "defaults": _ps.DEFAULTS}


@router.post("/api/planning-settings")
def save_planning_settings(updates: dict):
    merged = _ps.save(updates)
    _reset_live_caches()
    return {"ok": True, "settings": merged}


@router.post("/api/reset")
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


@router.get("/api/health")
def health():
    return {"status": "ok", "source": _get_data().get("_source", "synthetic")}


@router.get("/api/health/db")
def health_db():
    from ..integration.db import test_connection
    return test_connection()
