"""Data-Quality Gate (blueprint Section 5.3).

Every load is scored for completeness, validity and timeliness. Critical defects
(missing BOM, missing lead time on a critical RM, negative on-hand, stale price)
block the affected SKUs from planning and raise a data-stewardship exception to
the owning function -- rather than silently producing a wrong plan.
"""
from __future__ import annotations

# severity -> whether it blocks planning for the affected object
CRITICAL = "critical"   # blocks planning
WARNING = "warning"     # logged, does not block

# Human-readable catalogue of the defects the gate detects.
DEFECT_CATALOGUE = {
    "missing_bom":          (CRITICAL, "No bill of materials -- cannot explode to RM", "Planning / Eng"),
    "missing_bom_version":  (WARNING,  "BOM version not signed off (recentness)", "Planning / Eng"),
    "missing_lead_time":    (CRITICAL, "Missing lead time on a critical RM", "Procurement"),
    "negative_on_hand":     (CRITICAL, "Negative on-hand inventory (data error)", "Logistics / Warehouse"),
    "stale_price":          (CRITICAL, "Unit price not refreshed this cycle (stale)", "Finance"),
    "missing_projection":   (WARNING,  "No Sales projection submitted (auto-filled)", "Sales"),
    "below_soc":            (WARNING,  "Projection below firm Pending SOC", "Sales"),
    "supply_not_loaded":    (WARNING,  "RM/BOM/capacity not loaded yet (demand-only)", "Planning"),
}


def _score(passed: int, total: int) -> float:
    return round(passed / total, 3) if total else 1.0


def build_dq(data: dict) -> dict:
    """Run the gate over the current load; return per-object findings, the set
    of blocked SKUs/RMs, and load-level completeness/validity/timeliness scores."""
    skus = data["skus"]
    rms = data["rms"]
    bom = data["bom"]
    # In demand-only live mode the RM/BOM/capacity are placeholders, not real
    # loaded data -- so the gate must not block SKUs for "missing BOM" etc.
    supply_placeholder = data.get("_supply_placeholder", False)

    findings: list[dict] = []
    blocked_skus: set[str] = set()
    blocked_rms: set[str] = set()

    def add(scope, obj_id, name, defect, owner_fn=None):
        sev, desc, owner = DEFECT_CATALOGUE[defect]
        findings.append({
            "scope": scope, "id": obj_id, "name": name,
            "defect": defect, "severity": sev,
            "description": desc, "owner": owner_fn or owner,
        })
        if sev == CRITICAL:
            (blocked_skus if scope == "SKU" else blocked_rms).add(obj_id)

    # ---- per-RM checks first (a blocked RM cascades to its consuming SKUs) ----
    # Skipped entirely when RMs are placeholders (demand-only live mode).
    if not supply_placeholder:
        for code, rm in rms.items():
            for d in rm.get("dq_defects", []):
                add("RM", code, rm["name"], d)
            if rm.get("lead_time_days") in (None, 0) and rm["criticality"] >= 0.8 and "missing_lead_time" not in rm.get("dq_defects", []):
                add("RM", code, rm["name"], "missing_lead_time")
            if rm.get("on_hand", 0) < 0 and "negative_on_hand" not in rm.get("dq_defects", []):
                add("RM", code, rm["name"], "negative_on_hand")

    # ---- per-SKU checks ----
    for sku_id, sku in skus.items():
        for d in sku.get("dq_defects", []):
            add("SKU", sku_id, sku["name"], d, owner_fn=sku["owner"] if d == "missing_projection" else None)
        # BOM checks only when supply data is loaded AND the item is manufactured
        # (traded items legitimately have no BOM -- not a defect).
        if not supply_placeholder and sku.get("is_manufactured", True):
            if sku_id not in bom or not bom[sku_id]:
                add("SKU", sku_id, sku["name"], "missing_bom")
            for (rm_code, *_rest) in bom.get(sku_id, []):
                if rm_code in blocked_rms:
                    blocked_skus.add(sku_id)
                    findings.append({
                        "scope": "SKU", "id": sku_id, "name": sku["name"],
                        "defect": "blocked_rm", "severity": CRITICAL,
                        "description": f"Consumes blocked RM {rm_code}", "owner": sku["owner"],
                    })
        if sku.get("projection") is None:
            add("SKU", sku_id, sku["name"], "missing_projection", owner_fn=sku["owner"])

    if supply_placeholder:
        findings.append({
            "scope": "LOAD", "id": "supply", "name": "RM / BOM / capacity",
            "defect": "supply_not_loaded", "severity": WARNING,
            "description": DEFECT_CATALOGUE["supply_not_loaded"][1], "owner": "Planning",
        })

    # ---- load-level dimension scores ----
    n_sku, n_rm = len(skus), len(rms)
    has_projection = sum(1 for s in skus.values() if s.get("projection") is not None)
    if supply_placeholder:
        # demand-only: score completeness on the demand data we actually load
        completeness = _score(has_projection, n_sku)
        validity = _score(n_sku - len(blocked_skus), n_sku)
    else:
        completeness = _score(has_projection + sum(1 for s in skus if s in bom), 2 * n_sku)
        validity = _score((n_sku - len(blocked_skus)) + (n_rm - len(blocked_rms)), n_sku + n_rm)
    # timeliness: refresh discipline proxy -- stale price is the only timeliness defect here
    stale = sum(1 for f in findings if f["defect"] == "stale_price")
    timeliness = _score(n_sku - stale, n_sku)

    critical_findings = [f for f in findings if f["severity"] == CRITICAL]
    return {
        "cycle_period": data["cycle_period"],
        "findings": findings,
        "blocked_skus": sorted(blocked_skus),
        "blocked_rms": sorted(blocked_rms),
        "scores": {
            "completeness": completeness,
            "validity": validity,
            "timeliness": timeliness,
            "overall": round((completeness + validity + timeliness) / 3, 3),
        },
        "summary": {
            "total_findings": len(findings),
            "critical": len(critical_findings),
            "warnings": len(findings) - len(critical_findings),
            "skus_blocked": len(blocked_skus),
            "rms_blocked": len(blocked_rms),
            "gate": "PASS" if not critical_findings else "BLOCKED",
        },
    }
