"""Advanced forecasting governance (blueprint Section 9).

Beyond per-series method selection (in ``baseline.py``), the blueprint requires:
  - Hierarchical reconciliation: SKU -> family -> region -> total, so the
    numbers add up at every level.
  - Champion / challenger: a new model must beat the incumbent on hold-out
    accuracy before it is promoted; everything is back-tested.
  - Demand sensing: short-horizon signals (open orders, Pending SOC velocity)
    refine the near weeks.
"""
from __future__ import annotations

from . import baseline as bl


# ---------------------------------------------------------------- reconciliation
def hierarchical_reconciliation(data: dict, baselines: dict) -> dict:
    """Aggregate SKU baselines bottom-up to family, region and total. Bottom-up
    is coherent by construction, so totals always add up; we surface the tree
    and each level's share for the demand review."""
    skus = data["skus"]
    total = 0.0
    families: dict[str, dict] = {}
    regions: dict[str, dict] = {}

    for sku_id, sku in skus.items():
        b = baselines[sku_id]["baseline"]
        total += b
        fam = families.setdefault(sku["family"], {"baseline": 0.0, "skus": 0, "region": sku["region"]})
        fam["baseline"] += b
        fam["skus"] += 1
        reg = regions.setdefault(sku["region"], {"baseline": 0.0, "skus": 0})
        reg["baseline"] += b
        reg["skus"] += 1

    family_rows = [
        {"family": f, "region": v["region"], "skus": v["skus"],
         "baseline": round(v["baseline"], 1),
         "share": round(v["baseline"] / total, 4) if total else 0.0}
        for f, v in sorted(families.items(), key=lambda kv: -kv[1]["baseline"])
    ]
    region_rows = [
        {"region": r, "skus": v["skus"], "baseline": round(v["baseline"], 1),
         "share": round(v["baseline"] / total, 4) if total else 0.0}
        for r, v in sorted(regions.items(), key=lambda kv: -kv[1]["baseline"])
    ]
    return {
        "total_baseline": round(total, 1),
        "by_family": family_rows,
        "by_region": region_rows,
        "coherent": True,  # bottom-up: sum(SKU) == sum(family) == sum(region) == total
    }


# ------------------------------------------------------------ champion/challenger
def _holdout_wmape(sku: dict, hist: list[dict], periods: list[str], method: str) -> float:
    """One-step-ahead hold-out WMAPE for a candidate method over the last 6 mo."""
    K = 6
    abs_err = total = 0.0
    for t in range(max(6, len(hist) - K), len(hist)):
        prior = hist[:t]
        actual = hist[t]["true_demand"] if hist[t]["event"] == "stockout" else hist[t]["shipped"]
        if method == "naive":
            fc = prior[-1]["shipped"]
        elif method == "moving_avg":
            window = [h["shipped"] for h in prior[-3:]]
            fc = sum(window) / len(window) if window else 0.0
        else:  # champion = the engine's selected statistical baseline
            fc = bl.build_baseline(sku, prior, periods[:t], periods[t])["baseline"]
        abs_err += abs(actual - fc)
        total += actual
    return (abs_err / total) if total else 0.0


def champion_challenger(data: dict, baselines: dict) -> dict:
    """Back-test the incumbent (champion) statistical baseline against simple
    challengers; flag SKUs where a challenger would win and should be promoted."""
    periods = data["history_periods"]
    rows = []
    promotions = 0
    for sku_id, sku in data["skus"].items():
        hist = data["history"][sku_id]
        champ = _holdout_wmape(sku, hist, periods, "champion")
        chal_naive = _holdout_wmape(sku, hist, periods, "naive")
        chal_ma = _holdout_wmape(sku, hist, periods, "moving_avg")
        best_chal, best_name = min((chal_naive, "Naive"), (chal_ma, "Moving avg"))
        promote = best_chal < champ - 0.02   # must beat by a margin
        if promote:
            promotions += 1
        rows.append({
            "sku": sku_id, "name": sku["name"],
            "champion_method": baselines[sku_id]["method"],
            "champion_wmape": round(champ, 3),
            "challenger": best_name,
            "challenger_wmape": round(best_chal, 3),
            "promote_challenger": promote,
            "delta": round(champ - best_chal, 3),
        })
    rows.sort(key=lambda r: -r["delta"])
    return {"rows": rows, "promotion_candidates": promotions}


# ----------------------------------------------------------------- demand sensing
def demand_sensing(data: dict, baselines: dict) -> dict:
    """Short-horizon near-week correction: blend the statistical baseline with
    firm-order velocity (Pending SOC + open backlog) for the next weeks."""
    rows = []
    for sku_id, sku in data["skus"].items():
        base = baselines[sku_id]["baseline"]
        # firm velocity proxy: pending SOC is committed; treat as a near-term floor
        firm = sku["pending_soc"]
        firm_ratio = (firm / base) if base else 0.0
        # if firm orders are running hot vs baseline, sense demand upward
        sensed = base
        signal = "steady"
        if firm_ratio > 0.55:
            sensed = base * 1.08
            signal = "accelerating"
        elif firm_ratio < 0.20 and base > 0:
            sensed = base * 0.95
            signal = "softening"
        rows.append({
            "sku": sku_id, "name": sku["name"],
            "baseline": round(base, 1),
            "firm_soc": firm,
            "firm_ratio": round(firm_ratio, 3),
            "sensed_near_term": round(sensed, 1),
            "signal": signal,
            "adjustment": round((sensed - base) / base, 3) if base else 0.0,
        })
    rows.sort(key=lambda r: -abs(r["adjustment"]))
    return {"rows": rows}


def build_forecasting(data: dict, baselines: dict) -> dict:
    return {
        "cycle_period": data["cycle_period"],
        "reconciliation": hierarchical_reconciliation(data, baselines),
        "champion_challenger": champion_challenger(data, baselines),
        "demand_sensing": demand_sensing(data, baselines),
    }
