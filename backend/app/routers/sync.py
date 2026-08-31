"""Data freshness + Refresh-now (Phase 4 of the sync-to-DB architecture).

The API never syncs CRM itself — it only reports staging freshness and queues a
refresh request that the worker (worker.py) picks up.
"""
from fastapi import APIRouter

from ..integration import staging

router = APIRouter()


@router.get("/api/sync-status")
def get_sync_status():
    """Planning context + per-source freshness for the 'data as of…' banner."""
    return staging.sync_status()


@router.post("/api/refresh")
def post_refresh(source: str = "all"):
    """Queue a 'Refresh now' request; the worker drains it within ~30s."""
    ok = staging.request_refresh(source)
    return {"ok": ok, "queued": ok, "source": source}
