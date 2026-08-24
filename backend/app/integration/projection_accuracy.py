"""Projection-accuracy computation: received PROJECTION (CRM business plan) vs ACTUAL
PRODUCTION (RM_Consumption output qty per unique job), joined by item description and
rolled up three ways -- item / division / product.

Metrics per row/group (all three families the planner asked for):
  * raw          : projected, actual, variance (= projected - actual), variance%
  * accuracy/bias: accuracy% = 100 - WMAPE (weighted), bias% = variance / actual * 100
  * error        : MAPE (mean item abs% error), WMAPE (sum|var| / sum actual)

Division = consumption 'Product Group' (fallback projection Segment2);
Product  = consumption 'Product Category' (fallback projection Segment3).
"""
from __future__ import annotations

from .planning_filter import _norm, _squash


def _r(x, n=1):
    return round(x, n) if x is not None else None


def _metrics(projected: float, actual: float, sum_abs: float | None = None,
             pcts: list | None = None) -> dict:
    """Core metric bundle. For a single item pass sum_abs=None (abs var derived from the
    pair); for a group pass sum_abs = sum of per-item |variance| and pcts = per-item
    abs% errors so MAPE/WMAPE reflect the members, not the netted totals."""
    var = projected - actual
    abs_var = abs(var) if sum_abs is None else sum_abs
    d = {
        "projected": _r(projected), "actual": _r(actual), "variance": _r(var),
        "variance_pct": _r(100 * var / actual) if actual else None,
        "bias_pct": _r(100 * var / actual) if actual else None,
        "abs_pct_err": _r(100 * abs(var) / actual) if actual else None,
        "wmape": _r(100 * abs_var / actual) if actual else None,
        "mape": (_r(sum(pcts) / len(pcts)) if pcts else
                 (_r(100 * abs(var) / actual) if actual else None)),
        "accuracy_pct": (_r(max(0.0, 100 - 100 * abs_var / actual)) if actual
                         else (100.0 if projected == 0 else 0.0)),
    }
    return d


def _status(projected: float, actual: float) -> str:
    if projected > 0 and actual > 0:
        return "Matched"
    if projected > 0:
        return "Projected, not produced"
    return "Produced, not projected"


def build(projection: dict, production: dict, scope: dict | None = None) -> dict:
    """projection: {NAME_UPPER: {name,current,segment2,segment3,...}} (projection_from_crm)
    production:  {item_key: {item_desc,division,product,subcat,actual,uom,jobs,item_code}}
    Returns {items, divisions, products, summary, scope}."""
    items: dict[str, dict] = {}

    def _row(key, desc, div, prod, sub=""):
        it = items.get(key)
        if it is None:
            it = items[key] = {"item_key": key, "item_desc": desc,
                               "item_code": "", "division": div or "Unmapped",
                               "product": prod or "Unmapped", "subcat": sub or "",
                               "uom": "", "jobs": 0, "projected": 0.0, "actual": 0.0}
        return it

    # actuals first (they own the division/product hierarchy)
    for k, p in (production or {}).items():
        it = _row(k, p.get("item_desc", ""), p.get("division"), p.get("product"),
                  p.get("subcat"))
        it["actual"] += p.get("actual", 0.0)
        it["jobs"] += p.get("jobs", 0)
        it["uom"] = p.get("uom") or it["uom"]
        it["item_code"] = p.get("item_code") or it["item_code"]

    # projection joined by squashed item description
    for pj in (projection or {}).values():
        k = _squash(pj.get("name"))
        if not k:
            continue
        it = items.get(k)
        if it is None:
            it = _row(k, _norm(pj.get("name")), pj.get("segment2"), pj.get("segment3"))
        it["projected"] += pj.get("current", 0.0)

    item_rows = []
    for it in items.values():
        # drop items with neither projection nor production — they carry no signal
        # (projection_from_crm keeps current=0 header rows when approved_only is off).
        if it["projected"] <= 0 and it["actual"] <= 0:
            continue
        m = _metrics(it["projected"], it["actual"])
        item_rows.append({**it, **m, "status": _status(it["projected"], it["actual"])})
    item_rows.sort(key=lambda r: -abs(r["variance"] or 0))

    # Accuracy is only well-defined where a forecast AND an actual both exist. Production
    # also contains intermediates / basic chemicals never in the FG demand plan, so we
    # measure accuracy on the MATCHED set and report the other two buckets as coverage.
    def _is_matched(it):
        return it["projected"] > 0 and it["actual"] > 0

    def _group(field):
        g: dict[str, dict] = {}
        for it in item_rows:
            key = it.get(field) or "Unmapped"
            e = g.setdefault(key, {"name": key, "projected": 0.0, "actual": 0.0,
                                   "m_proj": 0.0, "m_act": 0.0, "sum_abs": 0.0, "pcts": [],
                                   "n_items": 0, "n_matched": 0,
                                   "actual_all": 0.0, "m_actual": 0.0})
            e["projected"] += it["projected"]
            e["actual"] += it["actual"]
            e["actual_all"] += it["actual"]
            e["n_items"] += 1
            if _is_matched(it):                 # accuracy stats over matched only
                e["n_matched"] += 1
                e["m_proj"] += it["projected"]
                e["m_act"] += it["actual"]
                e["m_actual"] += it["actual"]
                e["sum_abs"] += abs(it["projected"] - it["actual"])
                e["pcts"].append(100 * abs(it["projected"] - it["actual"]) / it["actual"])
        out = []
        for e in g.values():
            if e["n_matched"]:
                m = _metrics(e["m_proj"], e["m_act"], sum_abs=e["sum_abs"], pcts=e["pcts"])
            else:                               # no forecast+actual overlap -> undefined
                m = {k: None for k in ("projected", "actual", "variance", "variance_pct",
                                       "bias_pct", "abs_pct_err", "wmape", "mape", "accuracy_pct")}
            out.append({"name": e["name"], "n_items": e["n_items"],
                        "n_matched": e["n_matched"],
                        "projected_all": _r(e["projected"]), "actual_all": _r(e["actual_all"]),
                        "coverage_pct": _r(100 * e["m_actual"] / e["actual_all"]) if e["actual_all"] else None,
                        **m})
        out.sort(key=lambda r: -(r["actual_all"] or 0))
        return out

    matched = [it for it in item_rows if _is_matched(it)]
    sproj = sum(it["projected"] for it in matched)
    sact = sum(it["actual"] for it in matched)
    sabs = sum(abs(it["projected"] - it["actual"]) for it in matched)
    pcts = [100 * abs(it["projected"] - it["actual"]) / it["actual"] for it in matched]
    tot_actual = sum(it["actual"] for it in item_rows)
    tot_proj = sum(it["projected"] for it in item_rows)
    matched_actual = sum(it["actual"] for it in matched)
    _sm = (_metrics(sproj, sact, sum_abs=sabs, pcts=pcts) if matched else
           {k: None for k in ("projected", "actual", "variance", "variance_pct", "bias_pct",
                              "abs_pct_err", "wmape", "mape", "accuracy_pct")})
    summary = {**_sm,
               "n_items": len(item_rows),
               "n_matched": len(matched),
               "n_proj_only": sum(1 for it in item_rows if it["status"] == "Projected, not produced"),
               "n_prod_only": sum(1 for it in item_rows if it["status"] == "Produced, not projected"),
               "projected_all": _r(tot_proj), "actual_all": _r(tot_actual),
               "matched_actual": _r(matched_actual),
               "coverage_pct": _r(100 * matched_actual / tot_actual) if tot_actual else None}

    return {"items": item_rows, "divisions": _group("division"),
            "products": _group("product"), "summary": summary,
            "scope": scope or {}}
