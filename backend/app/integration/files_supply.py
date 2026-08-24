"""Assemble the engine supply side (RM master + BOM) from the Excel/CSV files.

Replaces the Oracle staging path: stock agings + BOM + PO receipts are loaded
from files and stitched into the shapes the planning engine consumes:
  - bom:  FG/assembly -> [(component, qty, scrap, yield), ...]  (single-level)
  - rms:  component -> RM master (cost, lead time, suppliers, criticality, ...)

Capacity master is not in any file, so RCCP runs with no bottleneck assets
(reported as a load note) rather than inventing capacity.
"""
from __future__ import annotations

from . import bom_file, po_file


def _flatten_to_leaves(assembly: str, bom_map: dict, depth: int = 0,
                       seen: frozenset = frozenset()) -> list[tuple]:
    """Recursively explode an assembly to its leaf (raw-material) components,
    multiplying quantities down the levels. Stops at items with no BOM (leaves),
    on cycles, or at a depth cap (Section 10.1 multi-level explosion)."""
    if assembly in seen or depth > 12:
        return []
    comps = bom_map.get(assembly)
    if not comps:
        return [(assembly, 1.0)]            # leaf raw material
    seen = seen | {assembly}
    out: list[tuple] = []
    for (comp, qty, _scrap, _yld) in comps:
        for (leaf, lqty) in _flatten_to_leaves(comp, bom_map, depth + 1, seen):
            out.append((leaf, qty * lqty))
    return out


def _criticality(suppliers: int, lead_days: float) -> float:
    crit = 0.4
    if suppliers <= 1:
        crit += 0.30          # single / sole source
    if lead_days >= 30:
        crit += 0.20          # long lead
    elif lead_days >= 21:
        crit += 0.10
    return round(min(crit, 0.99), 2)


def build_file_supply(sku_ids: set[str], stock: dict[str, dict],
                      bom_path: str | None, po_path: str | None,
                      warnings: list[str]) -> dict:
    """Return {bom, rms, assets, family_rate} from the files for in-scope FGs."""
    bom_data = {"bom": {}, "comp_desc": {}, "assemblies": set()}
    if bom_path:
        try:
            bom_data = bom_file.load_bom(bom_path)
            warnings.append(f"bom_file: {len(bom_data['bom'])} assemblies, "
                            f"{len(bom_data['components'])} components loaded.")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"bom_file: {type(e).__name__}: {str(e).splitlines()[0][:160]}")

    po = {}
    if po_path:
        try:
            po = po_file.load_po(po_path)
            warnings.append(f"po_file: {len(po)} purchased items loaded.")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"po_file: {type(e).__name__}: {str(e).splitlines()[0][:160]}")

    # multi-level BOM: explode each in-scope manufactured FG to leaf raw materials
    full_bom = bom_data["bom"]
    bom: dict[str, list] = {}
    rm_codes: set[str] = set()
    manufactured = 0
    multilevel = 0
    for sku in sku_ids:
        if sku not in full_bom:
            continue
        manufactured += 1
        leaves: dict[str, float] = {}
        for (leaf, qty) in _flatten_to_leaves(sku, full_bom):
            if leaf == sku:
                continue
            leaves[leaf] = leaves.get(leaf, 0.0) + qty
        if not leaves:                      # fallback to direct components
            leaves = {c: q for (c, q, *_r) in full_bom[sku]}
        else:
            # did any component need a further level of explosion?
            direct = {c for (c, *_r) in full_bom[sku]}
            if set(leaves) - direct:
                multilevel += 1
        bom[sku] = [(comp, round(qty, 4), 0.0, 1.0) for comp, qty in leaves.items()]
        rm_codes.update(leaves)

    # build the RM master for every component referenced by in-scope BOMs
    rms: dict[str, dict] = {}
    for code in rm_codes:
        st = stock.get(code, {})
        p = po.get(code, {})
        suppliers = p.get("suppliers", 1)
        lead = p.get("lead_time_days", 21.0)
        rms[code] = {
            "code": code,
            "name": bom_data["comp_desc"].get(code, code),
            "lead_time_days": int(round(lead)),
            "lead_time_variability": p.get("lead_time_variability", 0.25),
            "suppliers": suppliers,
            "criticality": _criticality(suppliers, lead),
            "hazard": "None",
            "unit_cost": p.get("unit_cost") or st.get("unit_cost") or 1.0,
            "moq": p.get("moq", 100.0),
            "shelf_life_days": st.get("shelf_life_days") or 999,
            "on_hand": round(st.get("on_hand", 0.0), 1),
            "open_po": 0.0,
            "purchased": code in po,
        }

    warnings.append(f"file-supply: {manufactured} of {len(sku_ids)} in-scope FGs are "
                    f"manufactured (have a BOM); {len(rms)} distinct RMs "
                    f"({multilevel} via multi-level explosion).")
    # capacity (assets) comes from the cycle-time file, applied by the adapter.
    return {"bom": bom, "rms": rms, "assets": {}, "family_rate": {}}
