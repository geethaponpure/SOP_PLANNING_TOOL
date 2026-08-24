"""BOM loader (BOM_Planning_Tool.xlsx -- Oracle BOM extract).

Maps each manufactured assembly to its direct components and quantities. The
file is multi-level (some components are themselves assemblies); this loader
returns the single-level structure the planning engine consumes, plus the
assembly/component universes so the adapter can tell made vs bought items apart.
"""
from __future__ import annotations


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def load_bom(path: str) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rit = ws.iter_rows(values_only=True)
    header = list(next(rit))
    ix = {n: i for i, n in enumerate(header)}

    def c(row, name):
        return row[ix[name]] if name in ix else None

    # assembly -> {component -> [qty, substitute, desc]}  (summed over seq lines)
    bom: dict[str, dict[str, list]] = {}
    comp_desc: dict[str, str] = {}
    asm_desc: dict[str, str] = {}
    for row in rit:
        asm = c(row, "ASSEMBLY_ITEM")
        comp = c(row, "COMPONENT_ITEM")
        if not asm or not comp:
            continue
        if c(row, "DISABLE_DATE"):       # skip disabled BOM lines
            continue
        qty = _num(c(row, "COMPONENT_QUANTITY"))
        if qty <= 0:
            continue
        d = bom.setdefault(asm, {})
        if comp in d:
            d[comp][0] += qty
        else:
            d[comp] = [qty, c(row, "SUBSTITUTE_ITEM"), c(row, "COMP_ITEM_DESC")]
        comp_desc[comp] = c(row, "COMP_ITEM_DESC") or comp
        asm_desc[asm] = c(row, "ASSEMBLY_DESC") or asm

    # flatten to engine shape: assembly -> [(component, qty, scrap, yield), ...]
    bom_flat: dict[str, list] = {}
    substitutes: dict[str, dict[str, str]] = {}
    for asm, comps in bom.items():
        rows = []
        for comp, (qty, sub, _desc) in comps.items():
            rows.append((comp, round(qty, 4), 0.0, 1.0))  # scrap/yield not in file
            if sub:
                substitutes.setdefault(asm, {})[comp] = sub
        bom_flat[asm] = rows

    return {
        "bom": bom_flat,
        "substitutes": substitutes,
        "assemblies": set(bom_flat),
        "components": set(comp_desc),
        "comp_desc": comp_desc,
        "asm_desc": asm_desc,
    }
