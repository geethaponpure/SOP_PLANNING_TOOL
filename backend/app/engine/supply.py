"""Supply & raw-material planning (blueprint Section 10).

Consumes ONLY the confirmed/consensus demand number (design rule: an
unvalidated CRM number never reaches procurement or production):

  net FG demand -> route PTO/PTS -> statistical safety stock ->
  explode BOM to gross RM -> net RM vs on-hand + open PO ->
  rough-cut capacity check against bottleneck assets.
"""
from __future__ import annotations

import math

# z-values for segment service-level targets (Section 10.2)
SERVICE_Z = {"A": 2.33, "B": 1.65, "C": 1.28}   # ~99% / 95% / 90%
SERVICE_LEVEL = {"A": 0.99, "B": 0.95, "C": 0.90}


def safety_stock(seg_abc: str, monthly_sd: float, lead_days: int,
                 lead_var: float = 0.0, monthly_demand: float = 0.0) -> dict:
    """Statistical safety stock protecting demand + lead-time variability.

    SS = z * sqrt( LT_months * sigma_d^2 + (d * sigma_LT)^2 )
    """
    z = SERVICE_Z.get(seg_abc, 1.65)
    lt_months = max(lead_days / 30.0, 0.25)
    sigma_lt_months = lt_months * lead_var
    demand_var = lt_months * (monthly_sd ** 2)
    lt_demand_var = (monthly_demand * sigma_lt_months) ** 2
    ss = z * math.sqrt(max(demand_var + lt_demand_var, 0.0))
    return {
        "safety_stock": round(ss, 1),
        "z": z,
        "service_level": SERVICE_LEVEL.get(seg_abc, 0.95),
        "safety_lead_time_days": round(lead_days * lead_var, 1),
    }


def _lot_size(qty: float, batch: int) -> float:
    """Round a production requirement up to a whole batch / minimum run
    (Section 10.4 batch & campaign sizing)."""
    if qty <= 0 or batch <= 0:
        return round(qty, 1)
    lots = math.ceil(qty / batch)
    return float(lots * batch)


def _shelf_life_cap(demand: float, shelf_life_days: int) -> float:
    """Cap PTS stock holding so it cannot exceed what is consumable within the
    shelf life (FEFO discipline, Section 10.4). Returns the max sellable buffer
    in addition to one period of demand."""
    # months of stock the shelf life can safely cover (release-adjusted)
    months_cover = max(0.5, shelf_life_days / 30.0)
    return demand * months_cover


def build_supply_plan(data: dict, baselines: dict, segmentation: dict,
                      consensus: dict[str, float], blocked: set[str] | None = None) -> dict:
    """consensus: sku -> locked demand quantity for the cycle period.
    blocked: SKU/RM ids failing the DQ gate -- excluded from planning (5.3)."""
    blocked = blocked or set()
    skus = data["skus"]
    fg_rows = []
    co_product_rows = []
    gross_rm: dict[str, float] = {c: 0.0 for c in data["rms"]}

    for sku_id, sku in skus.items():
        if sku_id in blocked:
            fg_rows.append({
                "sku": sku_id, "name": sku["name"], "family": sku["family"],
                "cell": segmentation["abc_xyz"][sku_id]["cell"],
                "policy": segmentation["fg_policy"][sku_id]["policy"],
                "blocked": True, "block_reason": "DQ gate -- critical defect",
                "consensus_demand": None, "on_hand": sku["on_hand"],
                "available": None, "safety_stock": None, "net_requirement": None,
                "lot_sized_qty": None, "constraints": ["DQ-BLOCKED"],
            })
            continue
        demand = consensus.get(sku_id)
        if demand is None:
            demand = baselines[sku_id]["baseline"]
        seg = segmentation["abc_xyz"][sku_id]
        pol = segmentation["fg_policy"][sku_id]["policy"]

        on_hand = sku["on_hand"]
        in_transit = sku["in_transit"]
        allocated = sku["allocated"]
        available = on_hand + in_transit - allocated

        # statistical safety stock (PTS only; PTO carries none)
        monthly_sd = baselines[sku_id]["cov"] * baselines[sku_id]["mean_demand"]
        constraints: list[str] = []
        # service-level uplift: items mostly sold to A/B-class (key) customers are
        # protected one service tier higher (Section 10.2 service by segment/tier).
        eff_abc = seg["abc"]
        key_share = sku.get("key_customer_share") or 0.0
        if key_share >= 0.5 and eff_abc in ("B", "C"):
            eff_abc = {"B": "A", "C": "B"}[eff_abc]
            constraints.append(f"service uplift (key-customer {int(key_share*100)}%)")
        ss_info = safety_stock(eff_abc, monthly_sd, sku["production_lead_time_days"])
        ss = ss_info["safety_stock"] if pol == "PTS" else 0.0

        if pol == "PTS":
            # shelf-life cap on how much PTS buffer we may hold (10.4)
            cap = _shelf_life_cap(demand, sku["shelf_life_days"])
            target = demand + ss
            if target > cap:
                target = cap
                constraints.append(f"shelf-life cap ({sku['shelf_life_days']}d)")
            net_req = max(0.0, target - available)
        else:
            # PTO: build only the confirmed order (proxy: pending SOC), net of available
            firm = sku["pending_soc"]
            net_req = max(0.0, firm - max(available, 0.0))

        # quality-release / quarantine adds to effective lead time (10.4)
        eff_lead = sku["production_lead_time_days"] + sku["quality_release_days"]

        # lot-size production up to a whole batch / minimum run (10.4)
        lot_sized = _lot_size(net_req, sku["batch_size"])
        if lot_sized > net_req and net_req > 0:
            constraints.append(f"lot-sized to {sku['batch_size']} batch")

        # explode BOM on the LOT-SIZED build quantity (apply scrap + yield)
        for (rm_code, qty, scrap, yld) in data["bom"].get(sku_id, []):
            gross = lot_sized * qty * (1 + scrap) / max(yld, 0.01)
            gross_rm[rm_code] += gross

        # co-product yield -- planned jointly (10.4)
        co = sku.get("co_product")
        if co and lot_sized > 0:
            co_qty = round(lot_sized * co["ratio"], 1)
            co_product_rows.append({
                "from_sku": sku_id, "co_product": co["name"],
                "ratio": co["ratio"], "quantity": co_qty,
            })

        fg_rows.append({
            "sku": sku_id, "name": sku["name"], "family": sku["family"],
            "cell": seg["cell"], "policy": pol, "blocked": False,
            "consensus_demand": round(demand, 1),
            "on_hand": on_hand, "in_transit": in_transit, "allocated": allocated,
            "available": round(available, 1),
            "safety_stock": round(ss, 1),
            "service_level": ss_info["service_level"],
            "net_requirement": round(net_req, 1),
            "lot_sized_qty": round(lot_sized, 1),
            "batch_size": sku["batch_size"],
            "customer_tier": sku.get("customer_tier"),
            "key_customer_share": sku.get("key_customer_share"),
            "service_uplift": eff_abc != seg["abc"],
            "equipment": sku.get("equipment"),
            "cycle_time_per_batch": sku.get("cycle_time_per_batch"),
            "quality_release_days": sku["quality_release_days"],
            "effective_lead_days": eff_lead,
            "constraints": constraints,
        })

    # ----- net RM requirements vs on-hand + open PO -----
    rm_rows = []
    for code, rm in data["rms"].items():
        gross = gross_rm[code]
        rpol = segmentation["rm_policy"][code]
        if code in blocked:
            rm_rows.append({
                "code": code, "name": rm["name"], "policy": rpol["policy"],
                "kraljic": rpol["kraljic"], "supply_risk": rpol["supply_risk"],
                "criticality": rm["criticality"], "blocked": True,
                "block_reason": "DQ gate -- critical defect",
                "gross_requirement": round(gross, 1), "net_buy": None,
                "ordered_qty": None, "buy_value": 0, "below_moq": False,
                "lead_time_days": rm["lead_time_days"], "suppliers": rm["suppliers"],
                "review": rpol["review"],
            })
            continue
        # strategic/bottleneck RMs carry a time-based safety buffer
        buffer = 0.0
        safety_lead = 0.0
        if rpol["policy"] == "PTS":
            buffer = gross * (rm["lead_time_days"] / 30.0) * 0.5  # ~half a lead time of cover
            # safety lead time supplements safety stock for long-lead RM (10.2)
            safety_lead = round(rm["lead_time_days"] * rm["lead_time_variability"], 1)
        available = rm["on_hand"] + rm["open_po"]
        net_buy = max(0.0, gross + buffer - available)
        # round the actual order up to MOQ (10.4 / procurement reality)
        ordered = 0.0
        if net_buy > 0:
            ordered = max(net_buy, rm["moq"]) if net_buy < rm["moq"] else math.ceil(net_buy / rm["moq"]) * rm["moq"]
        rm_rows.append({
            "code": code, "name": rm["name"], "blocked": False,
            "policy": rpol["policy"], "kraljic": rpol["kraljic"],
            "supply_risk": rpol["supply_risk"],
            "criticality": rm["criticality"],
            "lead_time_days": rm["lead_time_days"],
            "safety_lead_time_days": safety_lead,
            "suppliers": rm["suppliers"],
            "gross_requirement": round(gross, 1),
            "safety_buffer": round(buffer, 1),
            "on_hand": rm["on_hand"], "open_po": rm["open_po"],
            "net_buy": round(net_buy, 1),
            "moq": rm["moq"],
            "ordered_qty": round(ordered, 1),
            "below_moq": 0 < net_buy < rm["moq"],
            "buy_value": round(ordered * rm["unit_cost"], 0),
            "review": rpol["review"],
        })
    rm_rows.sort(key=lambda r: (-r["criticality"], -(r["buy_value"] or 0)))

    rccp = rough_cut_capacity(data, fg_rows)

    total_buy_value = sum(r["buy_value"] or 0 for r in rm_rows)
    return {
        "cycle_period": data["cycle_period"],
        "fg_plan": fg_rows,
        "rm_plan": rm_rows,
        "co_products": co_product_rows,
        "rccp": rccp,
        "summary": {
            "total_net_fg": round(sum(r["net_requirement"] or 0 for r in fg_rows), 1),
            "total_rm_buy_value": round(total_buy_value, 0),
            "critical_rm_to_buy": sum(1 for r in rm_rows if r["criticality"] >= 0.8 and (r.get("net_buy") or 0) > 0),
            "capacity_gaps": sum(1 for a in rccp if a["overloaded"]),
            "skus_blocked": sum(1 for r in fg_rows if r.get("blocked")),
            "rms_blocked": sum(1 for r in rm_rows if r.get("blocked")),
            "co_product_lines": len(co_product_rows),
        },
    }


def rough_cut_capacity(data: dict, fg_rows: list[dict]) -> list[dict]:
    """Load each bottleneck asset and flag periods where demand > capacity.

    Two modes: equipment-based (real cycle-time data -> load = batches x cycle
    time on the item's equipment) or family-based (rate per family on an asset).
    """
    assets = data.get("assets", {})
    if not assets:
        return []
    equipment_mode = all("families" not in a for a in assets.values())
    load: dict[str, float] = {a: 0.0 for a in assets}
    contrib: dict[str, list] = {a: [] for a in assets}   # per-asset item loads

    rate = data.get("family_rate", {})
    fam_asset = {}
    if not equipment_mode:
        for asset_id, a in assets.items():
            for fam in a.get("families", []):
                fam_asset[fam] = asset_id

    for row in fg_rows:
        if row.get("blocked"):
            continue
        build_qty = row.get("lot_sized_qty") or row.get("net_requirement") or 0.0
        if build_qty <= 0:
            continue
        if equipment_mode:
            eq = row.get("equipment")
            cyc = row.get("cycle_time_per_batch")
            batch = row.get("batch_size") or 0
            if not eq or eq not in load or not cyc or not batch:
                continue
            hrs = math.ceil(build_qty / batch) * cyc
        else:
            eq = fam_asset.get(row["family"])
            if not eq:
                continue
            hrs = build_qty / max(rate.get(row["family"], 1.0), 0.1)
        load[eq] += hrs
        contrib[eq].append({"sku": row["sku"], "name": row["name"],
                            "tier": row.get("customer_tier") or "—", "hours": round(hrs, 1)})

    # service-tier priority for allocation when an asset is overloaded
    rank = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "—": 5}
    out = []
    for asset_id, a in assets.items():
        used = load[asset_id]
        cap = a["hours"]
        util = used / cap if cap else 0.0
        allocation = []
        if util > 1.0:
            cum = 0.0
            for it in sorted(contrib[asset_id], key=lambda x: (rank.get(x["tier"], 5), -x["hours"])):
                cum += it["hours"]
                allocation.append({**it, "status": "scheduled" if cum <= cap else "deferred"})
        out.append({
            "asset": asset_id, "name": a["name"],
            "capacity_hours": cap,
            "load_hours": round(used, 1),
            "utilisation": round(util, 3),
            "overloaded": util > 1.0,
            "gap_hours": round(max(0.0, used - cap), 1),
            "n_items": len(contrib[asset_id]),
            "allocation": allocation,   # tier-prioritised when overloaded
            "options": (
                ["Allocate by tier (below)", "Campaign re-sequence", "Overtime", "Toll manufacture"]
                if util > 1.0 else []
            ),
        })
    out.sort(key=lambda r: -r["utilisation"])
    return out
