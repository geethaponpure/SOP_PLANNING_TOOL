"""Analytics & Intelligence roadmap (blueprint Section 12).

Layered descriptive -> diagnostic -> predictive -> prescriptive. Implemented
with transparent statistical scoring (no opaque ML dependency) so every flag is
explainable and tied to a decision and an owner:

  - Projection anomaly detection   (predictive, sharpens Section 7)
  - Stock-out & expiry risk scoring (predictive, ranked by value at risk)
  - Supplier reliability prediction (predictive, pre-empt line stoppage)
  - What-if scenario simulation     (prescriptive)
  - Prescriptive inventory & buy optimisation / MEIO (prescriptive)
  - Segmentation auto-tuning        (prescriptive policy refresh)
"""
from __future__ import annotations

import math
import statistics

from . import baseline as bl
from . import segmentation as seg
from . import supply as sup


# --------------------------------------------------- projection anomaly detection
def anomaly_detection(data: dict, baselines: dict) -> dict:
    """Flag Sales projections that deviate from the learned per-SKU demand
    distribution beyond what simple bands catch -- a robust z-score on the
    cleansed history. |z| >= 3 is a strong anomaly."""
    rows = []
    for sku_id, sku in data["skus"].items():
        proj = sku["projection"]
        if proj is None:
            continue
        series = bl.cleanse(data["history"][sku_id])
        nz = [v for v in series if v > 0]
        if len(nz) < 4:
            continue
        med = statistics.median(nz)
        # robust spread (MAD -> sigma)
        mad = statistics.median([abs(v - med) for v in nz]) or (statistics.pstdev(nz) or 1.0)
        sigma = 1.4826 * mad or 1.0
        z = (proj - med) / sigma
        score = abs(z)
        level = "anomaly" if score >= 3 else ("watch" if score >= 2 else "normal")
        if level == "normal":
            continue
        rows.append({
            "sku": sku_id, "name": sku["name"], "owner": sku["owner"],
            "projection": proj, "expected": round(med, 1),
            "z_score": round(z, 2), "level": level,
            "direction": "high" if z > 0 else "low",
            "value_at_risk": round(abs(proj - med) * sku["unit_value"], 0),
        })
    rows.sort(key=lambda r: -r["value_at_risk"])
    return {"rows": rows, "anomalies": sum(1 for r in rows if r["level"] == "anomaly")}


# ------------------------------------------------ stock-out & expiry risk scoring
def risk_scoring(data: dict, baselines: dict, segmentation: dict, supply: dict) -> dict:
    """Predictive flags: which SKUs are likely to stock out (cover < lead time)
    or expire (PTS holding beyond shelf life), ranked by value at risk."""
    fg_by_sku = {r["sku"]: r for r in supply["fg_plan"]}
    rows = []
    for sku_id, sku in data["skus"].items():
        row = fg_by_sku.get(sku_id, {})
        if row.get("blocked"):
            continue
        base = baselines[sku_id]["baseline"] or 0.0
        available = row.get("available") or 0.0
        daily = base / 30.0 if base else 0.0
        days_cover = (available / daily) if daily else 999
        lead = sku["production_lead_time_days"] + sku["quality_release_days"]

        # stock-out risk: cover shorter than replenishment lead time
        stockout_risk = 0.0
        if daily > 0:
            stockout_risk = max(0.0, min(1.0, (lead - days_cover) / lead))

        # expiry risk: PTS holding more days than shelf life supports
        pol = segmentation["fg_policy"][sku_id]["policy"]
        expiry_risk = 0.0
        if pol == "PTS" and daily > 0:
            shelf = sku["shelf_life_days"]
            expiry_risk = max(0.0, min(1.0, (days_cover - shelf) / shelf)) if days_cover > shelf else 0.0

        var = round(base * sku["unit_value"], 0)
        top = max(stockout_risk, expiry_risk)
        if top < 0.15:
            continue
        rows.append({
            "sku": sku_id, "name": sku["name"], "cell": row.get("cell"),
            "policy": pol, "days_cover": round(days_cover, 1),
            "lead_days": lead, "shelf_life_days": sku["shelf_life_days"],
            "stockout_risk": round(stockout_risk, 2),
            "expiry_risk": round(expiry_risk, 2),
            "value_at_risk": var,
            "flag": "stock-out" if stockout_risk >= expiry_risk else "expiry",
        })
    rows.sort(key=lambda r: -(max(r["stockout_risk"], r["expiry_risk"]) * r["value_at_risk"]))
    return {
        "rows": rows,
        "stockout_flags": sum(1 for r in rows if r["flag"] == "stock-out"),
        "expiry_flags": sum(1 for r in rows if r["flag"] == "expiry"),
    }


# ------------------------------------------------- supplier reliability prediction
def supplier_reliability(data: dict) -> dict:
    """Score the probability of a late RM delivery from delivery history and
    lead-time variability; pre-empt line stoppage on critical inputs."""
    rows = []
    for code, rm in data["rms"].items():
        otr = rm.get("on_time_rate", 1.0)
        late_prob = round(1 - otr, 3)
        # criticality-weighted exposure
        exposure = round(late_prob * rm["criticality"], 3)
        risk = "high" if exposure >= 0.30 else ("medium" if exposure >= 0.12 else "low")
        action = ("Qualify second source / raise safety lead time"
                  if risk == "high" else
                  "Monitor; confirm PO dates" if risk == "medium" else "None")
        rows.append({
            "code": code, "name": rm["name"],
            "suppliers": rm["suppliers"], "criticality": rm["criticality"],
            "on_time_rate": otr, "late_probability": late_prob,
            "planned_lead_days": rm["lead_time_days"],
            "avg_actual_lead_days": rm.get("avg_actual_lead_days"),
            "exposure": exposure, "risk": risk, "action": action,
        })
    rows.sort(key=lambda r: -r["exposure"])
    return {"rows": rows, "high_risk": sum(1 for r in rows if r["risk"] == "high")}


# ------------------------------------------------ prescriptive inventory / buy opt
def meio_optimisation(data: dict, baselines: dict, segmentation: dict, supply: dict) -> dict:
    """Coarse MEIO + optimal-buy guidance: where the policy holds too much/too
    little buffer relative to value-at-risk, recommend a move; recommend order
    timing/qty within MOQ & batch constraints."""
    recs = []
    for r in supply["fg_plan"]:
        if r.get("blocked"):
            continue
        sku = data["skus"][r["sku"]]
        # high-value short-shelf-life PTS holding -> push buffer upstream (postpone)
        if r["policy"] == "PTS" and sku["unit_value"] >= 5 and sku["shelf_life_days"] <= 540:
            recs.append({
                "scope": "FG", "id": r["sku"], "name": r["name"],
                "recommendation": "Hold buffer at intermediate (postpone) -- high value, short shelf life",
                "lever": "MEIO / postponement",
            })
        if r["policy"] == "PTS" and (r.get("safety_stock") or 0) > (r.get("consensus_demand") or 0):
            recs.append({
                "scope": "FG", "id": r["sku"], "name": r["name"],
                "recommendation": "Safety stock exceeds a month of demand -- review service target / MEIO pooling",
                "lever": "Safety-stock right-size",
            })
    for r in supply["rm_plan"]:
        if r.get("blocked") or not r.get("net_buy"):
            continue
        if r.get("below_moq"):
            recs.append({
                "scope": "RM", "id": r["code"], "name": r["name"],
                "recommendation": f"Net buy {r['net_buy']} < MOQ {r['moq']} -- order one MOQ now, defer next cycle",
                "lever": "MOQ-aware ordering",
            })
        if r["kraljic"] == "Strategic" and r.get("net_buy", 0) > 0:
            recs.append({
                "scope": "RM", "id": r["code"], "name": r["name"],
                "recommendation": "Strategic single/dual-source -- stage PO early with safety lead time",
                "lever": "Strategic pre-buy",
            })
    return {"recommendations": recs, "count": len(recs)}


# ----------------------------------------------------- segmentation auto-tuning
def segmentation_autotune(data: dict, baselines: dict, segmentation: dict) -> dict:
    """Re-run ABC-XYZ on the latest baselines and compare to the SKU's recorded
    policy, flagging drift that warrants a policy refresh."""
    fresh = seg.abc_xyz(data, baselines)
    rows = []
    drift = 0
    for sku_id, info in fresh.items():
        current = segmentation["abc_xyz"][sku_id]
        cur_pol = segmentation["fg_policy"][sku_id]["policy"]
        # recommended policy under the fresh cell
        rec_pol = seg.fg_policy(data["skus"][sku_id], info, baselines[sku_id])["policy"]
        changed = (info["cell"] != current["cell"]) or (rec_pol != cur_pol)
        if changed:
            drift += 1
            rows.append({
                "sku": sku_id, "name": data["skus"][sku_id]["name"],
                "current_cell": current["cell"], "recommended_cell": info["cell"],
                "current_policy": cur_pol, "recommended_policy": rec_pol,
                "cov": info["cov"],
            })
    return {"rows": rows, "drift_count": drift,
            "note": "Periodic re-segmentation; review flagged SKUs for policy refresh"}


# -------------------------------------------------------- what-if simulation
def what_if(data: dict, baselines: dict, segmentation: dict,
            consensus: dict[str, float], scenario: dict, blocked: set[str]) -> dict:
    """Model a scenario and report the service / cost / cash deltas vs the base
    plan before committing. Supported levers:
        demand_surge_pct  -- +/- % shock to all consensus demand
        family            -- restrict the surge to one family (optional)
        supplier_outage   -- RM code knocked out (set available -> 0)
        capacity_loss_pct -- reduce all bottleneck-asset hours by %
    """
    import copy
    base_plan = sup.build_supply_plan(data, baselines, segmentation, consensus, blocked)

    surge = scenario.get("demand_surge_pct", 0) / 100.0
    fam = scenario.get("family")
    outage = scenario.get("supplier_outage")
    cap_loss = scenario.get("capacity_loss_pct", 0) / 100.0

    # scenario consensus
    scn_consensus = {}
    for sku_id, q in consensus.items():
        if surge and (not fam or data["skus"][sku_id]["family"] == fam):
            scn_consensus[sku_id] = max(0.0, (q or 0) * (1 + surge))
        else:
            scn_consensus[sku_id] = q

    # scenario dataset (deep copy only what we mutate)
    scn_data = dict(data)
    if outage or cap_loss:
        scn_data = copy.deepcopy(data)
        if outage and outage in scn_data["rms"]:
            scn_data["rms"][outage]["on_hand"] = 0
            scn_data["rms"][outage]["open_po"] = 0
        if cap_loss:
            for a in scn_data["assets"].values():
                a["hours"] = a["hours"] * (1 - cap_loss)

    scn_plan = sup.build_supply_plan(scn_data, baselines, segmentation, scn_consensus, blocked)

    def _delta(key, sub="summary"):
        return round((scn_plan[sub][key] or 0) - (base_plan[sub][key] or 0), 1)

    # outage impact: which critical RMs now cannot be covered
    outage_impact = []
    if outage:
        for r in scn_plan["rm_plan"]:
            if r["code"] == outage:
                outage_impact.append({
                    "code": r["code"], "name": r["name"],
                    "gross_requirement": r["gross_requirement"],
                    "shortfall": r.get("net_buy"),
                })

    return {
        "scenario": scenario,
        "base": base_plan["summary"],
        "scenario_result": scn_plan["summary"],
        "deltas": {
            "net_fg": _delta("total_net_fg"),
            "rm_buy_value": _delta("total_rm_buy_value"),
            "capacity_gaps": _delta("capacity_gaps"),
            "critical_rm_to_buy": _delta("critical_rm_to_buy"),
        },
        "new_capacity_gaps": [a for a in scn_plan["rccp"] if a["overloaded"]],
        "outage_impact": outage_impact,
    }


def build_analytics(data: dict, baselines: dict, segmentation: dict, supply: dict) -> dict:
    """The descriptive/diagnostic/predictive/prescriptive bundle (non-interactive
    parts). What-if is exposed separately as it takes scenario parameters."""
    return {
        "cycle_period": data["cycle_period"],
        "maturity": [
            {"layer": "Descriptive", "question": "What happened?", "status": "live"},
            {"layer": "Diagnostic", "question": "Why did it happen?", "status": "live"},
            {"layer": "Predictive", "question": "What is likely next?", "status": "live"},
            {"layer": "Prescriptive", "question": "What should we do?", "status": "live"},
        ],
        "anomaly_detection": anomaly_detection(data, baselines),
        "risk_scoring": risk_scoring(data, baselines, segmentation, supply),
        "supplier_reliability": supplier_reliability(data),
        "meio": meio_optimisation(data, baselines, segmentation, supply),
        "segmentation_autotune": segmentation_autotune(data, baselines, segmentation),
    }
