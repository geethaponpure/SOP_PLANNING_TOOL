"""Statistical baseline + forecast-method selection (blueprint Sections 7.1, 9).

Pure-Python implementation: history is cleansed (stock-outs corrected to true
demand, one-off bulk deals dampened), then level / trend / seasonality are
derived and projected one period forward with a confidence interval. The
forecast method is chosen per series from its pattern.
"""
from __future__ import annotations

import math
import statistics


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def cleanse(history: list[dict]) -> list[float]:
    """Return a cleansed demand series from raw history.

    - stock-out periods: use latent ``true_demand`` instead of shipped
    - one-off bulk deals: dampen toward a robust local level (winsorise)
    """
    raw = []
    for h in history:
        if h.get("event") == "stockout":
            raw.append(h["true_demand"])
        else:
            raw.append(h["shipped"])

    if not raw:
        return raw
    # winsorise extreme highs (bulk deals) to the 90th-pct-ish cap
    ordered = sorted(raw)
    cap = ordered[int(0.9 * (len(ordered) - 1))]
    cap = max(cap, _mean(raw) * 1.8)
    return [min(v, cap) for v in raw]


def seasonal_indices(series: list[float], periods: list[str]) -> list[float]:
    """Month-of-year multiplicative seasonal indices (length 12, Jan..Dec)."""
    overall = _mean(series) or 1.0
    buckets: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for v, p in zip(series, periods):
        month = int(p.split("-")[1])
        buckets[month].append(v)
    idx = []
    for m in range(1, 13):
        b = buckets[m]
        mb = _mean(b)
        # neutral (1.0) for months with no data or zero demand, so a sparse
        # real series never produces a zero seasonal index (avoids /0 downstream)
        idx.append((mb / overall) if (b and mb > 0) else 1.0)
    # normalise so the indices average 1.0, then floor away from zero
    avg = _mean(idx) or 1.0
    return [max(v / avg, 0.05) for v in idx]


def _linear_trend(series: list[float]) -> tuple[float, float]:
    """Ordinary least-squares slope + intercept over index 0..n-1."""
    n = len(series)
    if n < 2:
        return 0.0, (series[0] if series else 0.0)
    xs = list(range(n))
    mx, my = _mean(xs), _mean(series)
    denom = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, series)) / denom
    intercept = my - slope * mx
    return slope, intercept


def _is_intermittent(series: list[float]) -> bool:
    nz = [v for v in series if v > 0.01 * (max(series) or 1)]
    return len(nz) < 0.6 * len(series)


def method_for(pattern: str, intermittent: bool, history_len: int) -> str:
    if history_len < 6:
        return "Analogue / attach-rate"
    if intermittent or pattern == "erratic":
        return "Croston / SBA (plan as PTO)"
    if pattern == "seasonal":
        return "Holt-Winters (seasonal)"
    if pattern == "variable":
        return "SARIMA / gradient-boosted"
    return "Exponential smoothing"


def build_baseline(sku: dict, history: list[dict], periods: list[str],
                   next_period: str) -> dict:
    """Compute the one-period-ahead statistical baseline for a SKU."""
    series = cleanse(history)
    nonzero = [v for v in series if v > 0]
    intermittent = _is_intermittent(series)

    # coefficient of variation drives XYZ classification later
    mean_d = _mean(nonzero) if nonzero else 0.0
    sd = statistics.pstdev(nonzero) if len(nonzero) > 1 else 0.0
    cov = (sd / mean_d) if mean_d else 0.0

    method = method_for(sku["pattern"], intermittent, len([h for h in history if h["shipped"] > 0]))

    next_month = int(next_period.split("-")[1])

    if intermittent or sku["pattern"] == "erratic":
        # Croston-style: average demand size * probability of occurrence
        occ = [v for v in series if v > 0]
        prob = len(occ) / len(series) if series else 0
        baseline = _mean(occ) * prob if occ else 0.0
        seas = [1.0] * 12
    else:
        # de-seasonalise -> trend -> re-seasonalise the next month
        seas = seasonal_indices(series, periods)
        deseason = [v / (seas[int(p.split("-")[1]) - 1] or 1.0) for v, p in zip(series, periods)]
        slope, intercept = _linear_trend(deseason)
        level_next = intercept + slope * len(series)
        baseline = max(0.0, level_next * seas[next_month - 1])

    # confidence interval from recent residual spread
    resid_sd = sd
    ci_low = max(0.0, baseline - 1.28 * resid_sd)   # ~80% one-sided-ish
    ci_high = baseline + 1.28 * resid_sd

    return {
        "baseline": round(baseline, 1),
        "ci_low": round(ci_low, 1),
        "ci_high": round(ci_high, 1),
        "cov": round(cov, 3),
        "mean_demand": round(mean_d, 1),
        "intermittent": intermittent,
        "method": method,
        "annual_volume": round(sum(series), 1),
    }


def build_all_baselines(data: dict) -> dict[str, dict]:
    out = {}
    for sku_id, sku in data["skus"].items():
        out[sku_id] = build_baseline(
            sku, data["history"][sku_id], data["history_periods"],
            data["cycle_period"],
        )
    return out
