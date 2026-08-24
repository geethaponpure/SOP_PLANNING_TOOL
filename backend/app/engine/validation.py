"""Demand quantity validation engine (blueprint Section 7).

Triangulates the CRM Sales projection against three independent references
(statistical baseline, firm Pending SOC, LMS projection), applies
segment-aware tolerance bands, enforces the SOC floor rule, and classifies
each disagreement into a structured exception with a suggested action.
"""
from __future__ import annotations

# Tolerance bands by segment (Section 7.3). band = acceptable +/- deviation of
# projection vs the consensus reference before an exception is raised.
SEGMENT_BANDS = {
    "A": {"band": 0.125, "severity": "hard",  "label": "A / critical (tight)"},
    "B": {"band": 0.225, "severity": "soft",  "label": "B (standard)"},
    "C": {"band": 0.400, "severity": "light", "label": "C / erratic (loose)"},
}


def _pct_dev(value: float, ref: float) -> float | None:
    if ref in (None, 0):
        return None
    return (value - ref) / ref


def consensus_candidate(baseline: float, lms: float, soc: float) -> float:
    """Engine-proposed defensible number: weighted baseline+LMS, floored at SOC."""
    blended = 0.6 * baseline + 0.4 * lms
    return round(max(blended, soc), 1)


def classify(proj, baseline, soc, lms, seg_cell: str, intermittent: bool) -> dict:
    """Return the exception classification for one SKU-period."""
    abc = seg_cell[0]
    band_cfg = SEGMENT_BANDS[abc]
    band = band_cfg["band"]

    candidate = consensus_candidate(baseline, lms, soc)

    # --- stale / missing projection ---
    if proj is None:
        return {
            "type": "Stale / missing",
            "severity": "soft",
            "trigger": "No projection submitted",
            "meaning": "Process gap -- Sales owner did not submit",
            "action": "Auto-fill with baseline; chase owner",
            "auto_fill": baseline,
            "candidate": candidate,
            "within_band": False,
            "band": band,
            "dev_vs_candidate": None,
            "floor_ok": baseline >= soc,
        }

    dev_base = _pct_dev(proj, baseline)
    dev_soc = _pct_dev(proj, soc)
    dev_lms = _pct_dev(proj, lms)
    dev_cand = _pct_dev(proj, candidate)

    floor_ok = proj >= soc  # must cover firm commitments

    # --- under-projection: below firm SOC or far below baseline ---
    if not floor_ok or (dev_base is not None and dev_base < -band):
        return {
            "type": "Under-projection",
            "severity": "hard" if not floor_ok else band_cfg["severity"],
            "trigger": "Projection < Pending SOC" if not floor_ok else "Projection << baseline",
            "meaning": "Missed pipeline / conservative bias",
            "action": "Revise up to cover firm demand",
            "candidate": candidate,
            "within_band": False,
            "band": band,
            "dev_vs_candidate": _round(dev_cand),
            "dev_vs_baseline": _round(dev_base),
            "dev_vs_soc": _round(dev_soc),
            "dev_vs_lms": _round(dev_lms),
            "floor_ok": floor_ok,
        }

    # --- signal conflict: CRM and LMS diverge sharply (and disagree on direction) ---
    if dev_lms is not None and abs(dev_lms) > 0.30:
        return {
            "type": "Signal conflict",
            "severity": "soft",
            "trigger": "CRM vs LMS diverge sharply",
            "meaning": "Source / timing mismatch",
            "action": "Reconcile sources; pick defensible value (consensus candidate)",
            "candidate": candidate,
            "within_band": abs(dev_cand) <= band if dev_cand is not None else False,
            "band": band,
            "dev_vs_candidate": _round(dev_cand),
            "dev_vs_baseline": _round(dev_base),
            "dev_vs_soc": _round(dev_soc),
            "dev_vs_lms": _round(dev_lms),
            "floor_ok": floor_ok,
        }

    # --- over-projection: well above baseline AND above LMS ---
    if dev_base is not None and dev_base > band and (dev_lms is None or dev_lms > band):
        return {
            "type": "Over-projection",
            "severity": band_cfg["severity"],
            "trigger": "Projection >> baseline & >> LMS",
            "meaning": "Optimism / sandbagging / double-count",
            "action": "Sales confirms driver (deal, promo) or revises down",
            "candidate": candidate,
            "within_band": False,
            "band": band,
            "dev_vs_candidate": _round(dev_cand),
            "dev_vs_baseline": _round(dev_base),
            "dev_vs_soc": _round(dev_soc),
            "dev_vs_lms": _round(dev_lms),
            "floor_ok": floor_ok,
        }

    # --- erratic / lumpy: inherently hard to forecast ---
    if intermittent:
        within = abs(dev_cand) <= band if dev_cand is not None else True
        return {
            "type": "Erratic / lumpy" if not within else "Auto-accept",
            "severity": "light",
            "trigger": "High CoV, intermittent demand",
            "meaning": "Inherently hard to forecast",
            "action": "Shift to PTO; plan on order",
            "candidate": candidate,
            "within_band": within,
            "band": band,
            "dev_vs_candidate": _round(dev_cand),
            "dev_vs_baseline": _round(dev_base),
            "dev_vs_soc": _round(dev_soc),
            "dev_vs_lms": _round(dev_lms),
            "floor_ok": floor_ok,
        }

    # --- within band -> auto-accept ---
    within = abs(dev_cand) <= band if dev_cand is not None else True
    return {
        "type": "Auto-accept" if within else "Over-projection",
        "severity": "none" if within else band_cfg["severity"],
        "trigger": "Within tolerance band" if within else "Outside band",
        "meaning": "Projection credible vs references" if within else "Outside acceptable deviation",
        "action": "Accept into consensus" if within else "Sales review",
        "candidate": candidate,
        "within_band": within,
        "band": band,
        "dev_vs_candidate": _round(dev_cand),
        "dev_vs_baseline": _round(dev_base),
        "dev_vs_soc": _round(dev_soc),
        "dev_vs_lms": _round(dev_lms),
        "floor_ok": floor_ok,
    }


def _round(x):
    return None if x is None else round(x, 3)


def avg_dispatched_sales(history: list[dict], n: int = 3) -> float:
    """Trailing average of actual dispatched sales (SP_DespatchDetailsReport),
    over the last ``n`` months. Uses true demand on stockout months."""
    if not history:
        return 0.0
    recent = history[-n:]
    vals = [(h.get("true_demand") if h.get("event") == "stockout" else h.get("shipped", 0.0)) or 0.0
            for h in recent]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def sales_flag(projection, avg_sales: float, band: float) -> str:
    """Over/under-projected vs actual dispatched sales, within a tolerance band.
    'new' = an item with no trailing dispatched sales (new / NPI)."""
    if projection is None:
        return "none"            # no projection submitted
    if avg_sales <= 0:
        return "new"             # no trailing dispatched sales to compare against
    dev = (projection - avg_sales) / avg_sales
    if dev > band:
        return "over"            # projecting well above what actually sells
    if dev < -band:
        return "under"           # projecting well below what actually sells
    return "ontrack"


def owner_bias(data: dict, baselines: dict) -> dict[str, dict]:
    """Tracking-signal / bias guardrail: running projection bias per Sales owner
    across the portfolio (proxy: how their current projections compare to
    baseline, aggregated)."""
    agg: dict[str, dict] = {}
    for sku_id, sku in data["skus"].items():
        owner = sku["owner"]
        proj = sku["projection"]
        base = baselines[sku_id]["baseline"]
        a = agg.setdefault(owner, {"proj": 0.0, "base": 0.0, "n": 0, "missing": 0})
        if proj is None:
            a["missing"] += 1
            continue
        a["proj"] += proj
        a["base"] += base
        a["n"] += 1
    out = {}
    for owner, a in agg.items():
        bias = ((a["proj"] - a["base"]) / a["base"]) if a["base"] else 0.0
        flag = "persistently high" if bias > 0.10 else ("persistently low" if bias < -0.10 else "in tolerance")
        out[owner] = {
            "bias": round(bias, 3),
            "n_skus": a["n"],
            "missing": a["missing"],
            "flag": flag,
        }
    return out


def build_validation(data: dict, baselines: dict, segmentation: dict) -> dict:
    rows = []
    for sku_id, sku in data["skus"].items():
        b = baselines[sku_id]
        seg = segmentation["abc_xyz"][sku_id]
        cls = classify(
            sku["projection"], b["baseline"], sku["pending_soc"], sku["lms"],
            seg["cell"], b["intermittent"],
        )
        # --- actual dispatched sales (SP_DespatchDetailsReport) + projection-vs-sales flag ---
        proj = sku["projection"]
        avg_sales = avg_dispatched_sales(data["history"].get(sku_id, []))
        s_flag = sales_flag(proj, avg_sales, cls["band"])
        s_var = None if proj is None else round(proj - avg_sales, 1)
        s_var_pct = (None if proj is None or avg_sales <= 0
                     else round((proj - avg_sales) / avg_sales * 100, 1))
        segs = sku.get("segments", {})
        rows.append({
            "sku": sku_id,
            "name": sku["name"],
            "family": sku["family"],
            "owner": sku["owner"],
            "segment1": segs.get("Segment1") or "",
            "segment2": segs.get("Segment2") or "",
            "segment3": segs.get("Segment3") or "",
            "cell": seg["cell"],
            "projection": proj,
            "baseline": b["baseline"],
            "ci_low": b["ci_low"],
            "ci_high": b["ci_high"],
            "pending_soc": sku["pending_soc"],
            "lms": sku["lms"],
            "avg_sales": avg_sales,            # trailing dispatched sales (CRM)
            "sales_flag": s_flag,              # over / under / ontrack / new vs sales
            "sales_variance": s_var,           # projection - avg sales
            "sales_variance_pct": s_var_pct,
            "method": b["method"],
            **cls,
        })
    rows.sort(key=lambda r: (
        {"hard": 0, "soft": 1, "light": 2, "none": 3}.get(r["severity"], 4),
        -(r["projection"] or 0) * data["skus"][r["sku"]]["unit_value"],
    ))
    return {
        "cycle_period": data["cycle_period"],
        "rows": rows,
        "owner_bias": owner_bias(data, baselines),
    }
