"""Background cache pre-warm.

The live-CRM reads (dataset + RM filtration + PO files) take ~30-120s the first
time. Warm the memoised caches in a background thread at startup so pages load
instantly once ready, instead of blocking on first navigation. Disable with
``PREWARM=0``.
"""
from __future__ import annotations

import os
import threading
import time

from .api.core import _get_data, _get_baselines, _get_segmentation, _get_dq
from .api.live import (
    _business_map, _rm_planning, _aged_rm, _proj_sales_live,
    _scorecard_live, _ppv_live, _adhoc_inputs,
)


def _prewarm():
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


def start_prewarm():
    if os.getenv("PREWARM", "1") != "0":
        threading.Thread(target=_prewarm, daemon=True, name="prewarm").start()
