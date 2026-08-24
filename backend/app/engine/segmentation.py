"""Segmentation framework (blueprint Section 6).

- ABC by value contribution, XYZ by demand variability (CoV) -> 9 cells.
- Finished-goods PTO/PTS recommendation from the decision factors.
- Raw-material independent PTO/PTS classification + Kraljic placement.
"""
from __future__ import annotations


def abc_xyz(data: dict, baselines: dict[str, dict]) -> dict[str, dict]:
    """Classify every SKU on ABC (cumulative value) and XYZ (CoV)."""
    skus = data["skus"]
    # annual value contribution = annual volume * unit value
    contrib = {
        s: baselines[s]["annual_volume"] * skus[s]["unit_value"]
        for s in skus
    }
    total = sum(contrib.values()) or 1.0
    ranked = sorted(contrib, key=lambda s: contrib[s], reverse=True)

    out: dict[str, dict] = {}
    cum = 0.0
    for s in ranked:
        cum += contrib[s]
        share = cum / total
        abc = "A" if share <= 0.70 else ("B" if share <= 0.90 else "C")
        cov = baselines[s]["cov"]
        xyz = "X" if cov <= 0.25 else ("Y" if cov <= 0.55 else "Z")
        out[s] = {
            "abc": abc, "xyz": xyz, "cell": abc + xyz,
            "value_contribution": round(contrib[s], 0),
            "value_share": round(contrib[s] / total, 4),
            "cov": cov,
        }
    return out


def fg_policy(sku: dict, seg: dict, baseline: dict) -> dict:
    """Recommend PTO vs PTS for a finished good and explain why.

    Scores the blueprint's decision factors; positive total -> PTS.
    """
    reasons: list[str] = []
    score = 0

    cell = seg["cell"]
    # demand pattern / variability
    if seg["xyz"] == "X":
        score += 2; reasons.append("stable demand (X) -> stock")
    elif seg["xyz"] == "Y":
        score += 0; reasons.append("variable demand (Y) -> neutral")
    else:
        score -= 2; reasons.append("erratic demand (Z) -> order")

    # volume / value contribution
    if seg["abc"] == "A":
        score += 1; reasons.append("high value (A) -> protect service via stock")
    elif seg["abc"] == "C":
        score -= 1; reasons.append("low value (C) -> avoid holding")

    # shelf life vs holding
    if sku["shelf_life_days"] < 240:
        score -= 2; reasons.append(f"short shelf life ({sku['shelf_life_days']}d) -> expiry risk in stock")
    elif sku["shelf_life_days"] >= 540:
        score += 1; reasons.append("long shelf life -> safe to stock")

    # value-at-risk / obsolescence cost
    if sku["unit_value"] >= 15:
        score -= 2; reasons.append("high unit value -> high obsolescence cost")

    # production lead time vs typical customer tolerance (longer LT -> harder PTO)
    if sku["production_lead_time_days"] >= 14:
        score += 1; reasons.append("long production lead time -> hard to make to order")

    # NPI / intermittent override
    if sku["pattern"] == "npi":
        policy = "PTO"
        reasons.append("new product -> make to order until demand proven")
        return {"policy": policy, "score": score, "reasons": reasons,
                "postponement": False}
    if baseline.get("intermittent"):
        policy = "PTO"
        reasons.append("intermittent series -> plan on order")
        return {"policy": policy, "score": score, "reasons": reasons,
                "postponement": False}

    policy = "PTS" if score >= 1 else "PTO"

    # postponement candidate: families with shared intermediates + mid variability
    postponement = (
        policy == "PTO" and seg["xyz"] == "Y"
        and sku["family"] in ("Coatings Resins", "Specialty Blends")
    )
    if postponement:
        reasons.append("shared intermediate -> hold intermediate (PTS) + finish to order (PTO)")

    return {"policy": policy, "score": score, "reasons": reasons,
            "postponement": postponement, "cell": cell}


def rm_policy(rm: dict) -> dict:
    """Independent RM PTO/PTS classification + Kraljic quadrant + default policy."""
    reasons: list[str] = []
    risk = 0  # supply risk score
    # lead time
    if rm["lead_time_days"] >= 28 or rm["lead_time_variability"] >= 0.4:
        risk += 2; reasons.append("long / volatile lead time")
    elif rm["lead_time_days"] <= 14:
        reasons.append("short lead time")
    # availability / sourcing
    if rm["suppliers"] == 1:
        risk += 2; reasons.append("single / sole source")
    elif rm["suppliers"] >= 3:
        risk -= 1; reasons.append("multiple suppliers")
    # criticality
    if rm["criticality"] >= 0.8:
        risk += 2; reasons.append("no substitute -> stops production if absent")
    elif rm["criticality"] <= 0.45:
        risk -= 1; reasons.append("substitutable / non-blocking")
    # hazard / regulatory
    if rm["hazard"] == "Toxic":
        risk += 1; reasons.append("controlled / restricted handling")

    supply_risk = "High" if risk >= 3 else ("Medium" if risk >= 1 else "Low")

    # profit impact -> annual spend proxy (unit_cost * moq as a coarse signal)
    spend = rm["unit_cost"] * rm["moq"]
    profit_impact = "High" if spend >= 25000 else "Low"

    # Kraljic quadrant
    high_risk = risk >= 2
    if profit_impact == "High" and high_risk:
        kraljic = "Strategic"; policy = "PTS"; buffer = "Safety stock + safety lead time; consignment/VMI"; review = "Weekly"
    elif profit_impact == "Low" and high_risk:
        kraljic = "Bottleneck"; policy = "PTS"; buffer = "Time-based buffer; qualify second source"; review = "Weekly"
    elif profit_impact == "High" and not high_risk:
        kraljic = "Leverage"; policy = "PTO"; buffer = "Minimal; contract pricing / JIT"; review = "Per order"
    else:
        kraljic = "Non-critical"; policy = "PTO"; buffer = "Low min-max, automated"; review = "Periodic"

    return {
        "policy": policy, "kraljic": kraljic, "supply_risk": supply_risk,
        "profit_impact": profit_impact, "buffer": buffer, "review": review,
        "risk_score": risk, "reasons": reasons,
    }


def build_segmentation(data: dict, baselines: dict[str, dict]) -> dict:
    seg = abc_xyz(data, baselines)
    fg = {s: fg_policy(data["skus"][s], seg[s], baselines[s]) for s in data["skus"]}
    rm = {c: rm_policy(data["rms"][c]) for c in data["rms"]}
    return {"abc_xyz": seg, "fg_policy": fg, "rm_policy": rm}
