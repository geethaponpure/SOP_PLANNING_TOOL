"""Synthetic chemical-SKU dataset for the S&OP planning tool.

Everything downstream (baseline, validation, segmentation, supply, KPIs) is
computed from the structures generated here. The generator is fully seeded, so
the dataset is identical on every run -- the demo loop is reproducible.

This stands in for the CRM (projection), ERP (Pending SOC, open orders,
inventory, BOM, lead times) and LMS (independent projection) feeds described in
the blueprint, plus the master data those feeds depend on.
"""
from __future__ import annotations

import math
import os
import random
from functools import lru_cache

# Planning calendar -------------------------------------------------------
# 24 months of closed history ending 2026-05; the live cycle plans 2026-06.
HISTORY_MONTHS = 24
CYCLE_PERIOD = "2026-06"


def _month_labels(end_year: int, end_month: int, count: int) -> list[str]:
    """Return ``count`` month labels (oldest first) ending at end_year/month."""
    labels: list[str] = []
    y, m = end_year, end_month
    for _ in range(count):
        labels.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(labels))


HISTORY_PERIODS = _month_labels(2026, 5, HISTORY_MONTHS)

# Seasonal shape by family (12 monthly multipliers, Jan..Dec). Chemicals used
# in coatings/agro peak in spring; winter blends peak late year.
_SEASONAL = {
    "Solvents":         [0.95, 0.95, 1.05, 1.10, 1.15, 1.10, 1.05, 1.00, 1.00, 0.95, 0.85, 0.85],
    "Coatings Resins":  [0.80, 0.85, 1.05, 1.20, 1.25, 1.20, 1.10, 1.00, 0.95, 0.90, 0.85, 0.85],
    "Adhesives":        [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00],
    "Catalysts":        [1.05, 1.00, 1.00, 0.95, 0.95, 0.95, 1.00, 1.00, 1.05, 1.05, 1.05, 0.95],
    "Specialty Blends": [0.90, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.15, 1.05, 0.95, 0.90, 0.90],
    "Winter Additives": [1.30, 1.20, 1.00, 0.80, 0.60, 0.50, 0.50, 0.60, 0.80, 1.10, 1.30, 1.40],
}

# Sales owners and a structural projection bias for each (drives the bias /
# tracking-signal guardrail in the validation engine).
SALES_OWNERS = {
    "R. Mehta":   {"bias": 1.18, "label": "optimistic"},      # persistently over-projects
    "S. Iyer":    {"bias": 0.90, "label": "conservative"},    # persistently under-projects
    "A. Khan":    {"bias": 1.02, "label": "accurate"},
    "L. Pereira": {"bias": 1.06, "label": "slightly high"},
}

# Master SKU definitions. ``proj_story`` forces an interesting validation case
# so the demo exercises every exception type.
#   bias_mult  -- multiplies the owner bias to shape the CRM projection
#   pattern    -- demand pattern used to size noise / variability
SKU_DEFS = [
    # sku, name, family, owner, base_units/mo, unit_value, shelf_life_d, hazard, prod_lt_d, pattern, proj_story
    ("FG-1001", "Acetone Tech Grade",       "Solvents",         "R. Mehta",   4200, 2.1,  720, "Flammable",  7,  "stable",       "over"),
    ("FG-1002", "Toluene Reagent",          "Solvents",         "R. Mehta",   3100, 2.6,  720, "Flammable",  7,  "stable",       "normal"),
    ("FG-1003", "Isopropanol 99%",          "Solvents",         "A. Khan",    5200, 1.8,  900, "Flammable",  6,  "stable",       "normal"),
    ("FG-1004", "MEK Solvent",              "Solvents",         "S. Iyer",    1800, 2.9,  540, "Flammable",  7,  "variable",     "under"),
    ("FG-2001", "Acrylic Resin AR-200",     "Coatings Resins",  "L. Pereira", 2600, 4.4,  365, "Irritant",   12, "seasonal",     "normal"),
    ("FG-2002", "Alkyd Resin AK-50",        "Coatings Resins",  "L. Pereira", 1900, 4.9,  300, "Irritant",   12, "seasonal",     "over"),
    ("FG-2003", "Epoxy Resin EP-828",       "Coatings Resins",  "A. Khan",    1400, 6.8,  365, "Irritant",   14, "variable",     "conflict"),
    ("FG-3001", "PU Adhesive Base",         "Adhesives",        "A. Khan",    2200, 5.2,  270, "Irritant",   10, "stable",       "normal"),
    ("FG-3002", "Cyanoacrylate CA-10",      "Adhesives",        "S. Iyer",     480, 9.5,  180, "Irritant",    9, "erratic",      "erratic"),
    ("FG-3003", "Hot-Melt EVA Grade",       "Adhesives",        "L. Pereira", 1600, 3.1,  540, "None",        8,  "stable",       "normal"),
    ("FG-4001", "Pd Catalyst 5% C",         "Catalysts",        "A. Khan",     120, 88.0, 365, "Toxic",      21, "erratic",      "erratic"),
    ("FG-4002", "Phase-Transfer Catalyst",  "Catalysts",        "R. Mehta",    340, 31.0, 365, "Toxic",      18, "variable",     "over"),
    ("FG-5001", "Coolant Blend HX-1",       "Specialty Blends", "L. Pereira", 3300, 3.4,  540, "Irritant",   9,  "seasonal",     "normal"),
    ("FG-5002", "Defoamer DF-7",            "Specialty Blends", "A. Khan",    1250, 6.1,  365, "None",        10, "variable",     "normal"),
    ("FG-5003", "Surfactant Blend SB-3",    "Specialty Blends", "S. Iyer",    2100, 4.0,  450, "Irritant",   9,  "stable",       "under"),
    ("FG-6001", "Anti-Freeze AF Premium",   "Winter Additives", "R. Mehta",   2800, 2.8,  720, "Irritant",   8,  "seasonal",     "over"),
    ("FG-6002", "De-Icer Concentrate",      "Winter Additives", "L. Pereira", 1500, 3.6,  720, "Irritant",   8,  "seasonal",     "normal"),
    ("FG-7001", "Bio-Solvent NPI",          "Solvents",         "A. Khan",     900, 3.9,  540, "None",        9,  "npi",          "npi"),
    ("FG-7002", "Green Resin NPI",          "Coatings Resins",  "L. Pereira",  600, 7.2,  365, "Irritant",   13, "npi",          "npi"),
    ("FG-8001", "Specialty Chelate SC-9",   "Specialty Blends", "S. Iyer",     260, 14.0, 365, "Toxic",      15, "erratic",      "stale"),
]

# Raw-material master. criticality + lead time + sourcing drive the independent
# RM PTO/PTS classification and the Kraljic placement.
#   code, name, lead_d, lead_var, n_suppliers, criticality(0-1), hazard, unit_cost, moq, shelf_life_d
RM_DEFS = [
    ("RM-01", "Propylene",            14, 0.20, 3, 0.4, "Flammable", 0.9,  20000, 999),
    ("RM-02", "Benzene Feedstock",    21, 0.35, 2, 0.7, "Toxic",     1.1,  18000, 999),
    ("RM-03", "Methanol",             10, 0.15, 4, 0.3, "Flammable", 0.6,  25000, 999),
    ("RM-04", "Acrylic Acid",         28, 0.40, 2, 0.8, "Irritant",  1.6,  12000, 540),
    ("RM-05", "Bisphenol-A",          35, 0.45, 1, 0.9, "Irritant",  2.2,  10000, 540),  # single source
    ("RM-06", "Isocyanate MDI",       30, 0.50, 2, 0.85,"Toxic",     2.8,   8000, 365),
    ("RM-07", "Palladium Salt",       45, 0.60, 1, 0.95,"Toxic",    140.0,    50, 365),  # strategic single-source
    ("RM-08", "EVA Copolymer",        18, 0.20, 3, 0.4, "None",      1.4,  10000, 720),
    ("RM-09", "Ethylene Glycol",      12, 0.18, 4, 0.5, "Irritant",  0.8,  30000, 999),
    ("RM-10", "Surfactant Base LAS",  16, 0.22, 3, 0.45,"Irritant",  1.2,  12000, 540),
    ("RM-11", "Silicone Antifoam",    22, 0.30, 2, 0.6, "None",      3.1,   3000, 540),
    ("RM-12", "Phase-Transfer Amine", 26, 0.38, 2, 0.75,"Toxic",     4.5,   2000, 365),
    ("RM-13", "Acetic Anhydride",     20, 0.28, 2, 0.65,"Irritant",  1.0,  15000, 540),
    ("RM-14", "Stabiliser Package",   24, 0.32, 2, 0.55,"Irritant",  2.0,   5000, 365),
    ("RM-15", "Activated Carbon",     30, 0.25, 2, 0.5, "None",      0.7,   8000, 999),
]

# Bill of materials: sku -> list of (rm_code, qty_per_unit, scrap_frac, yield_frac)
BOM = {
    "FG-1001": [("RM-01", 0.6, 0.02, 0.97), ("RM-03", 0.3, 0.01, 0.99)],
    "FG-1002": [("RM-02", 0.8, 0.02, 0.96)],
    "FG-1003": [("RM-03", 0.9, 0.01, 0.98)],
    "FG-1004": [("RM-01", 0.5, 0.02, 0.97), ("RM-13", 0.2, 0.01, 0.98)],
    "FG-2001": [("RM-04", 0.7, 0.03, 0.95), ("RM-14", 0.05, 0.01, 0.99)],
    "FG-2002": [("RM-04", 0.4, 0.03, 0.95), ("RM-13", 0.3, 0.02, 0.97)],
    "FG-2003": [("RM-05", 0.6, 0.03, 0.94), ("RM-14", 0.08, 0.01, 0.99)],
    "FG-3001": [("RM-06", 0.5, 0.02, 0.96), ("RM-14", 0.05, 0.01, 0.99)],
    "FG-3002": [("RM-06", 0.3, 0.02, 0.95), ("RM-12", 0.1, 0.01, 0.98)],
    "FG-3003": [("RM-08", 0.85, 0.02, 0.97)],
    "FG-4001": [("RM-07", 0.05, 0.01, 0.90), ("RM-15", 0.4, 0.02, 0.98)],
    "FG-4002": [("RM-12", 0.6, 0.02, 0.95), ("RM-13", 0.2, 0.01, 0.98)],
    "FG-5001": [("RM-09", 0.7, 0.02, 0.98), ("RM-14", 0.05, 0.01, 0.99)],
    "FG-5002": [("RM-11", 0.6, 0.02, 0.97)],
    "FG-5003": [("RM-10", 0.75, 0.02, 0.97)],
    "FG-6001": [("RM-09", 0.8, 0.02, 0.98), ("RM-14", 0.04, 0.01, 0.99)],
    "FG-6002": [("RM-09", 0.6, 0.02, 0.98), ("RM-03", 0.2, 0.01, 0.99)],
    "FG-7001": [("RM-03", 0.5, 0.02, 0.97), ("RM-15", 0.3, 0.02, 0.98)],
    "FG-7002": [("RM-04", 0.5, 0.03, 0.95), ("RM-14", 0.06, 0.01, 0.99)],
    "FG-8001": [("RM-12", 0.4, 0.02, 0.95), ("RM-14", 0.1, 0.01, 0.98)],
}

# Bottleneck assets for the rough-cut capacity check. hours = available
# capacity hours in the cycle period; rate = units producible per hour.
ASSETS = {
    "Reactor-A":  {"name": "Reactor A (solvents)",   "hours": 520, "families": ["Solvents"]},
    "Reactor-B":  {"name": "Reactor B (resins)",     "hours": 480, "families": ["Coatings Resins"]},
    "Blender-1":  {"name": "Blender Line 1",         "hours": 600, "families": ["Specialty Blends", "Winter Additives"]},
    "Reactor-C":  {"name": "Reactor C (specialty)",  "hours": 300, "families": ["Adhesives", "Catalysts"]},
}
# Throughput (units/hour) per family on its asset -- coarse RCCP rate.
FAMILY_RATE = {
    "Solvents": 12.0, "Coatings Resins": 7.0, "Adhesives": 8.0,
    "Catalysts": 3.0, "Specialty Blends": 9.0, "Winter Additives": 10.0,
}

# Sales regions for hierarchical reconciliation (Section 9). Each family is sold
# predominantly through one region in this synthetic book.
FAMILY_REGION = {
    "Solvents": "West", "Coatings Resins": "North", "Adhesives": "North",
    "Catalysts": "Export", "Specialty Blends": "West", "Winter Additives": "South",
}

# Minimum viable batch / lot multiple (units) per family -- production is
# lot-sized up to a whole batch (Section 10.4 batch / campaign sizing).
FAMILY_BATCH = {
    "Solvents": 500, "Coatings Resins": 300, "Adhesives": 250,
    "Catalysts": 20, "Specialty Blends": 300, "Winter Additives": 400,
}

# Co-products: producing the key SKU also yields a saleable co-product at a
# fixed ratio (Section 10.4 co-/by-products -- plan jointly).
CO_PRODUCTS = {
    "FG-1001": {"name": "Recovered Solvent Heavies", "ratio": 0.08},
    "FG-6001": {"name": "Glycol By-product", "ratio": 0.10},
}


def _pattern_noise(pattern: str) -> float:
    return {
        "stable": 0.07, "variable": 0.18, "seasonal": 0.12,
        "erratic": 0.55, "npi": 0.25,
    }.get(pattern, 0.15)


def _gen_history(rng: random.Random, base: float, family: str, pattern: str,
                 periods: list[str]) -> list[dict]:
    """Generate cleansed-vs-actual monthly history with trend, seasonality,
    noise, and occasional outliers/stock-outs (which the baseline later
    corrects)."""
    seasonal = _SEASONAL[family]
    noise = _pattern_noise(pattern)
    # gentle trend over the window
    trend = rng.uniform(-0.004, 0.010)
    rows = []
    n = len(periods)
    for i, p in enumerate(periods):
        month = int(p.split("-")[1])
        level = base * (1 + trend * (i - n / 2))
        s = seasonal[month - 1]
        if pattern == "npi":
            # ramp: no demand until ~10 months ago, then climb
            ramp = max(0.0, (i - (n - 11)) / 11)
            demand = base * ramp * s * (1 + rng.gauss(0, noise))
        elif pattern == "erratic":
            # intermittent: many near-zero months, occasional spikes
            if rng.random() < 0.45:
                demand = base * rng.uniform(0.0, 0.25)
            else:
                demand = base * s * rng.uniform(0.8, 2.2)
        else:
            demand = level * s * (1 + rng.gauss(0, noise))
        actual = max(0.0, demand)

        true_demand = actual
        event = None
        # inject a one-off bulk deal (outlier high)
        if pattern in ("stable", "variable", "seasonal") and rng.random() < 0.05:
            true_demand = actual * rng.uniform(1.8, 2.6)
            event = "bulk_deal"
        # inject a stock-out (shipped < true demand)
        elif pattern in ("stable", "seasonal") and rng.random() < 0.06:
            true_demand = actual
            actual = actual * rng.uniform(0.45, 0.7)
            event = "stockout"

        rows.append({
            "period": p,
            "shipped": round(actual, 1),       # what ERP recorded as shipped
            "true_demand": round(true_demand, 1),  # latent demand (for stockout correction)
            "event": event,
        })
    return rows


def build_dataset() -> dict:
    """Return the planning dataset.

    DATA_SOURCE=live  -> pull real CRM (and Oracle) feeds via the integration
    adapter. Anything else (default) -> the seeded synthetic dataset below.
    """
    if os.getenv("DATA_SOURCE", "synthetic").lower() == "live":
        from .integration.adapter import build_live_dataset
        return build_live_dataset()
    return _build_synthetic_dataset()


@lru_cache(maxsize=1)
def _build_synthetic_dataset() -> dict:
    rng = random.Random(20260616)

    skus: dict[str, dict] = {}
    history: dict[str, list[dict]] = {}

    for (sku, name, family, owner, base, value, shelf, hazard, prod_lt,
         pattern, story) in SKU_DEFS:
        hist = _gen_history(rng, base, family, pattern, HISTORY_PERIODS)
        history[sku] = hist

        # recent demand for projection shaping (avg of last 3 shipped)
        recent = [h["shipped"] for h in hist[-3:]]
        recent_avg = sum(recent) / len(recent) if recent else base

        owner_bias = SALES_OWNERS[owner]["bias"]
        seasonal_next = _SEASONAL[family][int(CYCLE_PERIOD.split("-")[1]) - 1]

        # ----- firm Pending SOC (committed orders already in the pipeline) -----
        soc = round(recent_avg * rng.uniform(0.25, 0.55), 1)

        # ----- LMS independent projection (close to baseline-ish) -----
        lms = round(recent_avg * seasonal_next * rng.uniform(0.92, 1.10), 1)

        # ----- CRM projection (shaped by owner bias + the forced story) -----
        proj = recent_avg * seasonal_next * owner_bias
        if story == "over":
            proj *= rng.uniform(1.35, 1.7)
        elif story == "under":
            proj *= rng.uniform(0.55, 0.7)
            soc = round(proj * rng.uniform(1.15, 1.4), 1)  # SOC exceeds projection
        elif story == "conflict":
            lms = round(recent_avg * seasonal_next * rng.uniform(1.4, 1.7), 1)  # LMS far from CRM
            proj *= rng.uniform(0.8, 0.9)
        elif story == "erratic":
            proj *= rng.uniform(0.6, 1.8)
        elif story == "npi":
            proj = base * rng.uniform(0.9, 1.3)  # no real history to anchor to
            lms = round(base * rng.uniform(0.7, 1.0), 1)
        elif story == "stale":
            proj = None  # Sales never submitted a projection

        projection = None if proj is None else round(max(0.0, proj), 1)

        # ----- inventory position -----
        on_hand = round(recent_avg * rng.uniform(0.3, 1.2), 1)
        in_transit = round(recent_avg * rng.uniform(0.0, 0.4), 1)
        allocated = round(soc * rng.uniform(0.2, 0.6), 1)

        # ----- quality-release / quarantine time before stock is sellable -----
        # toxic / catalyst grades need longer QC release (Section 10.4).
        quality_release_days = 14 if hazard == "Toxic" else (7 if family in ("Coatings Resins", "Specialty Blends") else 4)

        # ----- data-quality defects deliberately injected on a couple of SKUs --
        #   so the DQ gate (Section 5.3) has something to catch and block.
        dq_defects: list[str] = []
        if sku == "FG-8001":
            dq_defects.append("stale_price")        # price not refreshed this cycle
        if sku == "FG-7002":
            dq_defects.append("missing_bom_version")  # BOM version not signed off

        # customer service tier (A/B/C/D/E) + key-customer (A+B) demand share,
        # so tier-aware planning (service-level uplift, OTIF-by-tier) is demoable.
        _tier = "A" if value >= 15 else ("B" if value >= 5 else ("C" if value >= 3 else "D"))
        _key_share = round(rng.uniform(0.6, 0.95) if _tier in ("A", "B") else rng.uniform(0.0, 0.4), 2)

        skus[sku] = {
            "sku": sku, "name": name, "family": family, "owner": owner,
            "region": FAMILY_REGION[family],
            "customer_tier": _tier, "key_customer_share": _key_share,
            "uom": "MT", "unit_value": value, "shelf_life_days": shelf,
            "hazard": hazard, "production_lead_time_days": prod_lt,
            "quality_release_days": quality_release_days,
            "batch_size": FAMILY_BATCH[family],
            "co_product": CO_PRODUCTS.get(sku),
            "pattern": pattern, "story": story,
            "projection": projection,     # CRM
            "pending_soc": soc,           # ERP/CRM firm
            "lms": lms,                   # LMS feed
            "on_hand": on_hand, "in_transit": in_transit, "allocated": allocated,
            "recent_avg": round(recent_avg, 1),
            "dq_defects": dq_defects,
        }

    rms: dict[str, dict] = {}
    for (code, name, lead, lvar, nsup, crit, hazard, cost, moq, shelf) in RM_DEFS:
        # ~0.2-0.9 months of cover on hand (so the net-buy plan is non-trivial
        # and critical-RM triggers actually fire each cycle).
        on_hand = round(rng.uniform(0.15, 0.55) * moq * 0.1, 0)
        open_po = round(rng.uniform(0.0, 0.40) * moq * 0.1, 0)

        # ----- supplier delivery history -> on-time rate & actual-vs-planned LT -
        # higher lead-time variability => lower reliability (Section 12 supplier
        # reliability prediction). 12 recent PO lines per RM.
        deliveries = []
        for _ in range(12):
            slip = rng.gauss(0, lvar) * lead     # days late (can be early)
            actual_lt = max(1.0, lead + slip)
            on_time = actual_lt <= lead * 1.10   # within 10% tolerance
            deliveries.append({"actual_lead_days": round(actual_lt, 1),
                               "on_time": on_time})
        on_time_rate = sum(1 for d in deliveries if d["on_time"]) / len(deliveries)
        avg_actual_lt = sum(d["actual_lead_days"] for d in deliveries) / len(deliveries)

        # ----- inject DQ defects on two RMs (Section 5.3) -----
        dq_defects = []
        if code == "RM-05":                      # critical single-source w/ missing LT
            dq_defects.append("missing_lead_time")
        if code == "RM-11":
            on_hand = -on_hand                   # negative on-hand (data error)
            dq_defects.append("negative_on_hand")

        rms[code] = {
            "code": code, "name": name, "lead_time_days": lead,
            "lead_time_variability": lvar, "suppliers": nsup,
            "criticality": crit, "hazard": hazard, "unit_cost": cost,
            "moq": moq, "shelf_life_days": shelf,
            "on_hand": on_hand, "open_po": open_po,
            "deliveries": deliveries,
            "on_time_rate": round(on_time_rate, 3),
            "avg_actual_lead_days": round(avg_actual_lt, 1),
            "dq_defects": dq_defects,
        }

    return {
        "cycle_period": CYCLE_PERIOD,
        "history_periods": HISTORY_PERIODS,
        "skus": skus,
        "history": history,
        "rms": rms,
        "bom": BOM,
        "assets": ASSETS,
        "family_rate": FAMILY_RATE,
        "family_region": FAMILY_REGION,
        "family_batch": FAMILY_BATCH,
        "co_products": CO_PRODUCTS,
        "sales_owners": SALES_OWNERS,
    }
