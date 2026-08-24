"""Multi-period (JC-wise) plan — blueprint Section 3.2 (1-12 week MPS/RM layer).

Disaggregates the annual business plan across the 13 JCs of the fiscal year
(seasonality x working days), then runs a time-phased netting per item across the
JC horizon: projected on-hand carried forward, production sized per JC, BOM
exploded to RM by JC, and bottleneck capacity loaded per JC.
"""
from __future__ import annotations

import math
from datetime import date

from . import baseline as bl
from ..integration import jc_calendar

# safety-stock buffer factor by ABC (fraction of a JC's demand held as cover)
SS_FACTOR = {"A": 0.5, "B": 0.3, "C": 0.2}


def _annual_demand(sku: dict, base: dict) -> float:
    """Annual demand: business-plan budget if present, else annualized baseline."""
    ab = sku.get("annual_budget")
    return float(ab) if ab else round(base["baseline"] * 12, 1)


def build_jc_plan(data: dict, baselines: dict, segmentation: dict,
                  today: date | None = None) -> dict:
    today = today or date.today()
    all_jcs = jc_calendar.calendar()
    horizon = jc_calendar.horizon(today)
    hz_nums = [j["jc"] for j in horizon]
    hours_per_day = float(__import__("os").getenv("EQUIPMENT_HOURS_PER_DAY", "16"))

    fg_plan = []
    # per-JC accumulators
    jc_demand_tot = {n: 0.0 for n in hz_nums}
    jc_prod_tot = {n: 0.0 for n in hz_nums}
    jc_rm_value = {n: 0.0 for n in hz_nums}
    jc_equip_load: dict[int, dict[str, float]] = {n: {} for n in hz_nums}

    skus = data["skus"]
    for sku_id, sku in skus.items():
        base = baselines[sku_id]
        annual = _annual_demand(sku, base)
        if annual <= 0:
            continue
        seg = segmentation["abc_xyz"][sku_id]
        pol = segmentation["fg_policy"][sku_id]["policy"]

        # seasonal weight per JC (working days x seasonal factor), normalised over the year
        series = [h["shipped"] for h in data["history"][sku_id]]
        seas = bl.seasonal_indices(series, data["history_periods"])
        weights = {j["jc"]: j["working_days"] * jc_calendar.seasonal_factor(j, seas) for j in all_jcs}
        wsum = sum(weights.values()) or 1.0

        cov = base["cov"]
        ss_factor = SS_FACTOR.get(seg["abc"], 0.2)
        batch = sku.get("batch_size") or 1

        available = max(0.0, sku.get("on_hand", 0.0))
        cells = []
        for j in horizon:
            n = j["jc"]
            demand = round(annual * weights[n] / wsum, 1)
            ss = round(ss_factor * cov * demand, 1)
            if pol == "PTS":
                need = max(0.0, demand + ss - available)
                production = math.ceil(need / batch) * batch if need > 0 else 0.0
            else:  # PTO: make to the period's demand
                production = math.ceil(demand / batch) * batch if demand > 0 else 0.0
            ending = round(available + production - demand, 1)
            cells.append({"jc": n, "demand": demand, "production": round(production, 1),
                          "ending_on_hand": ending, "safety_stock": ss})
            available = max(0.0, ending)

            jc_demand_tot[n] += demand
            jc_prod_tot[n] += production
            # BOM -> RM value per JC
            for (rm_code, qty, scrap, yld) in data["bom"].get(sku_id, []):
                rm = data["rms"].get(rm_code)
                if rm:
                    gross = production * qty * (1 + scrap) / max(yld, 0.01)
                    jc_rm_value[n] += gross * rm.get("unit_cost", 0)
            # capacity load per JC (equipment-based)
            eq = sku.get("equipment")
            cyc = sku.get("cycle_time_per_batch")
            if eq and cyc and production > 0:
                jc_equip_load[n][eq] = jc_equip_load[n].get(eq, 0.0) + math.ceil(production / batch) * cyc

        if any(c["production"] > 0 for c in cells):
            fg_plan.append({
                "sku": sku_id, "name": sku["name"], "policy": pol,
                "cell": seg["cell"], "customer_tier": sku.get("customer_tier"),
                "annual_demand": round(annual, 1), "cells": cells,
            })

    # per-JC capacity summary (available hrs = working days x hours/day per equipment)
    by_jc = []
    for j in horizon:
        n = j["jc"]
        avail = j["working_days"] * hours_per_day
        loads = jc_equip_load[n]
        overloaded = [{"equipment": e, "load": round(h, 1), "capacity": round(avail, 1),
                       "util": round(h / avail, 2)} for e, h in loads.items() if h > avail]
        by_jc.append({
            "jc": n, "label": j["label"], "from": j["from"], "to": j["to"],
            "working_days": j["working_days"],
            "demand": round(jc_demand_tot[n], 1),
            "production": round(jc_prod_tot[n], 1),
            "rm_buy_value": round(jc_rm_value[n], 0),
            "equipment_loaded": len(loads),
            "overloaded": overloaded,
        })

    fg_plan.sort(key=lambda r: -r["annual_demand"])
    return {
        "today": today.isoformat(),
        "current_jc": jc_calendar.current_jc(today),
        "horizon": [j["jc"] for j in horizon],
        "jcs": horizon,
        "by_jc": by_jc,
        "fg": fg_plan,
        "summary": {
            "items_planned": len(fg_plan),
            "jcs_in_horizon": len(horizon),
            "total_production": round(sum(jc_prod_tot.values()), 1),
            "total_rm_buy_value": round(sum(jc_rm_value.values()), 0),
            "capacity_gaps": sum(len(b["overloaded"]) for b in by_jc),
        },
    }
