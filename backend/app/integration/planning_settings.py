"""Admin 'Planning Setting' — the filtration elements the admin selects/edits.

These drive the Supply & RM Planning Filtration Technique. Persisted to a JSON
file (PLANNING_SETTINGS path, default backend/planning_settings.json) so the
admin's choices survive a restart.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_PATH = os.getenv("PLANNING_SETTINGS") or str(
    Path(__file__).resolve().parents[2] / "planning_settings.json")

DEFAULTS = {
    # BOM selection-preference elements the admin can switch on/off
    "bom_prefer_pmo": True,            # 1. prefer ORGANIZATION_CODE = PMO
    "bom_prefer_bulk_hdlk": True,      # 2. prefer ASSEMBLY_ITEM containing BULK/HDLK
    "bom_prefer_creation_date": True,  # 3. prefer the newest BOM_CREATION_DATE
    "bom_prefer_primary": True,        # 4. prefer ALTERNATE_BOM_DESIGNATOR = Primary
    "consider_substitutes": True,      # include substitute items in availability
    # BOM substitutes have manual-entry errors: DM water and packing materials get listed
    # as substitutes for raw materials. Packing (item code starting 'P') is excluded from
    # substitutes automatically; DM-water codes are listed here (admin-editable) and also
    # excluded. Both are dropped from substitute availability so they can't inflate stock.
    "dm_water_codes": [
        "TDIHDN240DMWA001", "TD11HDBR00200016", "TD11BULK00000212",
        "TD11BULK00000322", "TD11BULK00000349", "MLPCNHDD023-9900",
    ],
    "decode_encoded_names": True,      # show decoded RM/intermediate names (COMP_ITEM_DESC)
    # projection (3-JC): which JCs to include in planning ("plan as individual also")
    "plan_current": True,
    "plan_next1": True,
    "plan_next2": True,
    "drop_all_zero_projection": True,  # drop products with all (enabled) JCs = 0
    "min_plan_qty": 25,                # plan an item only if a JC projection OR the
                                       # pending SOC exceeds this (KG); else negligible

    "soc_window_months": 0,            # pending-SOC look-back; 0 = "as on date" (ALL open
                                       # pending up to the day before the planning JC starts)
    "aged_rm_days": 90,                # Aged-RM page: RM older than N days is "aged"
    "projection_band": 0.2,            # over/under-projection tolerance vs 3-JC avg sales (±20%)
    "adhoc_window_days": 60,           # Adhoc page: open SOC look-back window (days)
    "critical_lead_days": 30,          # supplier criticality: lead time above this is "long"
    "ppv_std_fy": "2025-26",           # PPV standard year (weighted-avg-price baseline)
    "otd_tolerance": 0.25,             # supplier OTD: on-time if lead <= expected*(1+tol)+2
    # ── Item Receipt Schedule (Planner / WH / Production / QC / BU / Branch view) ──
    # Warehouse-available date = manufacturing completion + this many days.
    "receipt_std_lead_days": 3,
    # Standard logistic lead time (days) branch region-wise. Branch receipt date =
    # warehouse-available date + the selected region's lead time. Admin-editable.
    "receipt_logistic_leads": {
        "South": 2,   # TN, Kerala, Telangana, Karnataka, AP
        "West":  5,   # Maharashtra
        "North": 7,   # Gujarat, Rajasthan, Haryana
        "East":  7,   # West Bengal, Odisha
    },
    # State -> region membership (drives the region picker labels). These are
    # *logistics* regions, not strict geography (e.g. Gujarat is grouped under
    # North, Maharashtra under West per the branch lead-time table). Central /
    # North-East states are bucketed to the nearest dispatch hub; admin-editable.
    "receipt_region_states": {
        "South": ["Tamil Nadu", "Kerala", "Telangana", "Karnataka", "Andhra Pradesh",
                  "Puducherry", "Lakshadweep", "Andaman and Nicobar Islands"],
        "West":  ["Maharashtra", "Goa", "Dadra and Nagar Haveli and Daman and Diu"],
        "North": ["Gujarat", "Rajasthan", "Haryana", "Punjab", "Delhi",
                  "Uttar Pradesh", "Uttarakhand", "Himachal Pradesh",
                  "Jammu and Kashmir", "Ladakh", "Chandigarh", "Madhya Pradesh"],
        "East":  ["West Bengal", "Odisha", "Bihar", "Jharkhand", "Chhattisgarh",
                  "Assam", "Sikkim", "Arunachal Pradesh", "Nagaland", "Manipur",
                  "Mizoram", "Tripura", "Meghalaya"],
    },
    "max_products": 0,                 # 0 = no cap (build ALL non-zero projection FGs)
    "preprocessing_days": 7,           # added to avg RM lead time for JC-bucket planning
    # In-transit (open PO) source. 'crm' = live BiPoDetails open-PO balance (ordered −
    # received − cancelled), window-independent and includes not-yet-received POs.
    # 'file' = legacy PO_receipts register (date-windowed; can miss/duplicate receipts).
    "intransit_source": "crm",
    "intransit_po_months": 4,          # only POs placed within N months count as in-transit
    "blanket_po_qty": 500000,          # ignore PO lines ordered above this (blanket/framework contracts)
    # At these orgs only "Raw Material" business POs count as in-transit — GC1/GC2 and
    # other-business POs there are dropped (e.g. Madhavaram receives GC intermediates
    # that must not inflate RM in-transit). Admin-editable.
    "intransit_rm_only_orgs": [
        "PCM - Madhavaram MFG", "PCC - Madhavaram MFG", "PCC - Madhavaram Trading",
        "PCT - Madhavaram Trading", "PPC - Madhavaram",
    ],
    "raw_material_business": "Raw Material",
    # For RMs whose Business is NOT "Raw Material" (GC1/GC2/intermediates), only stock
    # held at THESE orgs counts as available (trading/depot stock elsewhere is ignored).
    # Admin-configurable: add/remove orgs to widen/narrow the intermediate stock pool.
    # Default = the 12 MFG plants + 8 selected trading orgs.
    "intermediate_stock_orgs": [
        "PCM - Madhavaram MFG", "PCM - Puzhal MFG", "POI - Alathur MFG",
        "PSM - Thervoykandigai MFG", "PCM - Illalor MFG", "PPM - Puzhal MFG",
        "PPM - Gummudipoondi MFG", "PPM - Illalor MFG", "POI - Hosur Mfg",
        "PPC - Bavla(MFG)", "PSM - Hosur Mfg", "PSM - Hosur Mfg (BM)",
        # selected trading orgs whose intermediate stock is usable for manufacturing
        "PCT - Puzhal Trading", "PCT - Illalor Trading", "POT - Hosur Trading",
        "PST - Hosur Trading", "PST - Hosur Trading (BM)", "PST - Thervoykandigai",
        "POI - Alathur", "PPC - Puzhal",
    ],
    # Planning considers pending SOC only for these dispatch inventory orgs
    # (FnScheduleDtlPending.INVENTORY_ORG_NAME) -> "MFG SOC Pending" used in Current.
    "mfg_soc_orgs": [
        "PPC - Puzhal", "PPC - Madhavaram", "PCT - Illalor Trading",
        "PST - Thervoykandigai", "PCT - Madhavaram Trading", "PCT - Puzhal Trading",
        "PST - Hosur Trading", "POT - Hosur Trading", "POI - Alathur",
        "PST - Hosur Trading (BM)",
    ],
    # Warehouse = the 12 MFG plants PLUS the 10 trading/dispatch orgs (same orgs as
    # mfg_soc_orgs). Warehouse stock offsets Mfg Required; branch = everything else.
    "warehouse_orgs": [
        "PCM - Madhavaram MFG", "PCM - Puzhal MFG", "POI - Alathur MFG",
        "PSM - Thervoykandigai MFG", "PCM - Illalor MFG", "PPM - Puzhal MFG",
        "PPM - Gummudipoondi MFG", "PPM - Illalor MFG", "POI - Hosur Mfg",
        "PPC - Bavla(MFG)", "PSM - Hosur Mfg", "PSM - Hosur Mfg (BM)",
        # trading / dispatch orgs (also the MFG-SOC orgs)
        "PCT - Puzhal Trading", "PCT - Illalor Trading", "PCT - Madhavaram Trading",
        "POT - Hosur Trading", "PST - Hosur Trading", "PST - Hosur Trading (BM)",
        "PST - Thervoykandigai", "POI - Alathur", "PPC - Puzhal", "PPC - Madhavaram",
    ],
    "rm_source_orgs": [
        "PPC - Puzhal", "PCT - Puzhal Trading", "PCT - Illalor Trading",
        "PCT - Madhavaram Trading", "PST - Thervoykandigai", "PCM - Madhavaram MFG",
        "PSM - Thervoykandigai MFG", "PCM - Puzhal MFG", "POI - Alathur MFG",
        "PCM - Illalor MFG", "PPM - Puzhal MFG", "POI - Hosur Mfg", "PPM - Illalor MFG",
        "POI - Alathur", "POT - Hosur Trading", "PCC - Madhavaram Trading",
        "PST - Hosur Trading", "PSM - Hosur Mfg (BM)", "PSM - Hosur Mfg", "PCM- madhavaram",
    ],
    "excluded_subinv": [
        "NO SALE", "Return", "Clr/NonSal", "Scrap", "Expired", "Color",
        "UNRECON", "Rework", "Rejected", "Re-Process",
    ],
    # Group-company (inter-company) PO vendors — their purchases are DROPPED from all
    # PO analytics (in-transit, net-to-buy, lead-time). Matched on Vendor Name after
    # whitespace-normalize + uppercase. Admin-configurable; add spelling variants too.
    # User Master (admin): only CRM users in these departments may be approved for the
    # planning tool — supply-chain / planning / manufacturing / warehouse / R&D staff.
    "app_allowed_departments": [
        "Warehouse", "Warehouse - Quality", "R&D", "Lab chemist", "Operations",
        "Production", "Sourcing", "Procurement", "Contract Logistics",
    ],
    "intercompany_vendors": [
        "PON PURE CHEMICAL INDIA PRIVATE LIMITED",
        "PON PURE SPECIALITY CHEMICAL PRIVATE LIMITED",
        "PON PURE SPECIALITY CHEMICAL PRIVATE LIMITED-MFG",
        "PURE CHEMICALS CO",
        "PURE CHEMICALS CO MFG",
        "PURE CHEMICALS PTE LTD",
        "PURE ORGANIC INDUSTRIES",
        "PURE ORGANIC INDUSTRIES TRADING",
        "COLOR CHEMICALS",
        "COLOR CHEMICALS AND DYES LLP",
        "COLOR CHEMICALS AND DYES PRIVATE LIMITED",
    ],
}


def load() -> dict:
    """Current settings (defaults overlaid with any saved overrides)."""
    s = dict(DEFAULTS)
    try:
        with open(_PATH, encoding="utf-8") as f:
            s.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return s


def save(settings: dict) -> dict:
    """Persist only known keys; return the merged result."""
    s = load()
    for k, v in settings.items():
        if k in DEFAULTS:
            s[k] = v
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    return s
