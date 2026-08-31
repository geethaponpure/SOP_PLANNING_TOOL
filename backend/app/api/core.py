"""Core dataset caches — the synthetic/base dataset and the engine passes
(baseline, segmentation, DQ, supply) plus the cycle/consensus helpers.

Every loader is memoised with ``lru_cache(maxsize=1)`` so the heavy first build
happens once per process; ``main.reset`` / ``live._reset_live_caches`` clear them.
"""
from __future__ import annotations

from functools import lru_cache

from ..data import build_dataset
from .. import db_state
from ..engine import baseline as bl
from ..engine import segmentation as seg
from ..engine import dq as dq_engine
from ..engine import supply as sp


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


def _live_cycle() -> str:
    try:
        return _get_data().get("cycle_period", "")
    except Exception:   # noqa: BLE001
        return ""


__all__ = [n for n, v in list(globals().items())
           if callable(v) and getattr(v, "__module__", None) == __name__ and not n.startswith("__")]
