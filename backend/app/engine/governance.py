"""Governance, RACI, alerting & S&OP/IBP cadence (blueprint Sections 13 & 14).

Turns the planning state into the collaboration layer the blueprint asks for:
  - 13.2 RACI for each planning-cycle activity
  - 13.3 Tiered alerts (FYI / action-required / escalation) + approval gates
  - 13.4 Communication cadence (the S&OP forums)
  - 14   Gated monthly cadence -- each step has entry/exit criteria; a step
         cannot start until the prior one is signed off.
  - 16   Risks & mitigations register
"""
from __future__ import annotations

# ---------------------------------------------------------------- 13.2 RACI
RACI = [
    {"activity": "Submit projection",        "R": "Sales",          "A": "Sales Head",      "C": "Marketing",            "I": "Planning"},
    {"activity": "Validate quantities",      "R": "Demand Planner", "A": "SC Head",         "C": "Sales",                "I": "Finance"},
    {"activity": "Confirm / revise projection", "R": "Sales",       "A": "Sales Head",      "C": "Planning",             "I": "Finance"},
    {"activity": "Lock consensus demand",    "R": "Demand Planner", "A": "SC Head",         "C": "Sales, Finance",       "I": "Production"},
    {"activity": "Net & build supply/RM plan", "R": "Supply Planner", "A": "SC Head",       "C": "Production, Procurement", "I": "Finance"},
    {"activity": "RCCP & balance",           "R": "Supply Planner", "A": "SC Head",         "C": "Production",            "I": "Sales"},
    {"activity": "Approve S&OP plan",        "R": "SC Head",        "A": "Leadership / GM", "C": "All functions",        "I": "All"},
    {"activity": "Trigger critical-RM buy",  "R": "Procurement",    "A": "Procurement Head","C": "Planning",             "I": "Finance"},
]

# ---------------------------------------------------------------- 13.4 cadence
FORUMS = [
    {"forum": "Data & projection close",   "frequency": "Monthly (early cycle)", "purpose": "Lock inputs; run validation",        "owner": "Demand Planning"},
    {"forum": "Demand review",             "frequency": "Monthly",               "purpose": "Agree consensus demand; clear exceptions", "owner": "Demand Planning + Sales"},
    {"forum": "Supply review",             "frequency": "Monthly",               "purpose": "Feasibility, RCCP, RM gaps",          "owner": "Supply Planning + Production/Procurement"},
    {"forum": "Pre-S&OP reconciliation",   "frequency": "Monthly",               "purpose": "Resolve gaps, prepare scenarios",     "owner": "SC Head"},
    {"forum": "Executive S&OP / IBP",      "frequency": "Monthly",               "purpose": "Approve plan; trade-off decisions",   "owner": "Leadership"},
    {"forum": "Critical-RM & exception huddle", "frequency": "Weekly",           "purpose": "Long-lead / critical risks",          "owner": "Procurement + Planning"},
]

# ---------------------------------------------------------------- 16 risks
RISKS = [
    {"risk": "Poor master data (BOM, lead times)", "impact": "Wrong plans, lost trust",  "mitigation": "DQ gate; stewardship owners; block planning on critical defects"},
    {"risk": "Sales gaming the tolerance bands",   "impact": "Bias persists",            "mitigation": "Bias/tracking-signal guardrails; FVA accountability; reason-code review"},
    {"risk": "Over-stocking to hit service",       "impact": "Working capital tied up",  "mitigation": "Segment service targets; turns & E&O in the same review"},
    {"risk": "LMS / CRM signal conflict unresolved","impact": "Ambiguous demand",        "mitigation": "Defined reconciliation rule; SOC as firm floor; planner adjudication"},
    {"risk": "Critical RM single-source shock",    "impact": "Line stoppage",            "mitigation": "Strategic buffer + safety lead time; dual-source; supplier risk scoring"},
    {"risk": "Tool used as a report, not a decision system", "impact": "No behaviour change", "mitigation": "Workflow + RACI + leadership cadence; exceptions as owned tasks"},
    {"risk": "Shelf-life / expiry on PTS items",   "impact": "Write-offs",               "mitigation": "FEFO, shelf-life caps in policy, expiry risk analytics"},
]

# approval thresholds: overrides above this value-at-risk need named e-approval
APPROVAL_VALUE_THRESHOLD = 50000


def _alerts(data, dq, validation, supply, locked) -> list[dict]:
    """Tiered alert inbox: info (FYI) / action (SLA) / escalation."""
    alerts: list[dict] = []

    def add(tier, owner, title, detail, sku=None):
        alerts.append({"tier": tier, "owner": owner, "title": title,
                       "detail": detail, "sku": sku})

    # critical DQ defects -> escalation to stewardship owner
    for f in dq["findings"]:
        if f["severity"] == "critical":
            add("escalation", f["owner"], f"DQ block: {f['id']}", f["description"], f.get("id"))

    # hard validation exceptions -> action-required to Sales
    for r in validation["rows"]:
        if r["severity"] == "hard":
            var = round((r["projection"] or 0) * data["skus"][r["sku"]]["unit_value"], 0)
            tier = "escalation" if var >= APPROVAL_VALUE_THRESHOLD else "action"
            add(tier, r["owner"], f"{r['type']}: {r['sku']}", f"{r['action']} (value-at-risk ${var:,.0f})", r["sku"])
        elif r["severity"] == "soft":
            add("action", r["owner"], f"{r['type']}: {r['sku']}", r["action"], r["sku"])

    # capacity gaps -> action to Production
    for a in supply["rccp"]:
        if a["overloaded"]:
            add("escalation", "Production", f"Capacity gap: {a['name']}",
                f"Load {a['utilisation']*100:.0f}% of capacity; options: {', '.join(a['options'][:2])}…")

    # critical RM to buy -> action to Procurement
    for r in supply["rm_plan"]:
        if not r.get("blocked") and r["criticality"] >= 0.8 and (r.get("net_buy") or 0) > 0:
            add("action", "Procurement", f"Critical-RM buy: {r['code']}",
                f"Net buy {r['net_buy']} {r['name']}; lead {r['lead_time_days']}d")

    # owner bias guardrail -> FYI to Sales owner
    for owner, b in validation["owner_bias"].items():
        if b["flag"] != "in tolerance":
            add("info", owner, f"Forecast bias: {owner}",
                f"{b['flag']} ({b['bias']*100:+.0f}% vs baseline across {b['n_skus']} SKUs)")

    if not locked:
        add("info", "Demand Planner", "Consensus not locked",
            "Demand plan is still open; lock before supply publishes.")
    return alerts


def _cadence_gates(dq, validation, supply, locked) -> list[dict]:
    """Section 14 gated steps. Each gate's exit criteria must be met before the
    next step may start."""
    dq_pass = dq["summary"]["gate"] == "PASS"
    open_ex = sum(1 for r in validation["rows"] if r["severity"] == "hard")
    gaps = supply["summary"]["capacity_gaps"]

    steps = [
        {
            "step": "1. Product / portfolio review",
            "entry": "NPI & phase-out decisions captured",
            "exit": "Portfolio changes reflected in demand",
            "status": "complete",
            "owner": "Marketing + Planning",
        },
        {
            "step": "2. Demand review",
            "entry": "DQ gate passed; baseline built",
            "exit": "Exceptions cleared; consensus locked",
            "status": "complete" if locked else ("in_progress" if dq_pass else "blocked"),
            "owner": "Demand Planning + Sales",
            "detail": (f"{open_ex} hard exception(s) open" if open_ex else "no hard exceptions")
                      + ("; locked" if locked else "; open"),
        },
        {
            "step": "3. Supply review",
            "entry": "Consensus demand locked",
            "exit": "Net plan + RCCP complete; RM gaps identified",
            "status": "in_progress" if locked else "waiting",
            "owner": "Supply Planning + Production/Procurement",
            "detail": f"{gaps} capacity gap(s); {supply['summary']['critical_rm_to_buy']} critical RM to buy",
        },
        {
            "step": "4. Reconciliation / pre-S&OP",
            "entry": "Supply plan drafted",
            "exit": "Gaps quantified; scenarios prepared",
            "status": "waiting",
            "owner": "SC Head",
        },
        {
            "step": "5. Executive S&OP / IBP",
            "entry": "Scenarios & recommendations ready",
            "exit": "One plan approved; actions & owners committed",
            "status": "waiting",
            "owner": "Leadership",
        },
    ]
    return steps


def build_governance(data, dq, validation, supply, locked) -> dict:
    alerts = _alerts(data, dq, validation, supply, locked)
    tiers = {"escalation": 0, "action": 0, "info": 0}
    for a in alerts:
        tiers[a["tier"]] = tiers.get(a["tier"], 0) + 1
    return {
        "cycle_period": data["cycle_period"],
        "raci": RACI,
        "forums": FORUMS,
        "risks": RISKS,
        "alerts": alerts,
        "alert_tiers": tiers,
        "gates": _cadence_gates(dq, validation, supply, locked),
        "approval_threshold": APPROVAL_VALUE_THRESHOLD,
        "maturity_path": "Volume-based S&OP → financially-integrated IBP (one number for ops & finance)",
    }
