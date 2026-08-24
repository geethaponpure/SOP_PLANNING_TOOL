"""Adhoc planning engine.

Adhoc demand = firm open-SOC quantity for items that are NOT in the projection /
forecast but came in during the JC period. Each adhoc item's BOM is exploded to
raw materials, netted against current RM stock, and consolidated into the extra
RM to purchase.

In the engine dataset every SKU carries ``pending_soc`` (firm SOC) and
``projection`` (the planned number). An item is treated as *adhoc* when it has
firm SOC demand but no (or zero) projection.

Output shape matches the Adhoc Planning page:
  - products[]        : SOC item, qty, adhoc flag, BOM-exploded components
  - consolidated_rm[] : per-RM gross / stock / net-to-buy across adhoc items
  - summary           : counts + total RM to buy
"""
from __future__ import annotations

import os


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_adhoc_plan(data: dict, baselines: dict | None = None) -> dict:
    skus = data.get("skus", {})
    bom = data.get("bom", {})
    rms = data.get("rms", {})
    decode = True

    products: list[dict] = []
    cons: dict[str, dict] = {}

    for sku_id, sku in skus.items():
        soc_qty = round(_num(sku.get("pending_soc")), 1)
        if soc_qty <= 0:
            continue
        projection = _num(sku.get("projection"))
        is_adhoc = projection <= 0          # firm SOC but not projected -> adhoc
        recipe = bom.get(sku_id, [])
        has_bom = bool(recipe)

        comps: list[dict] = []
        for entry in recipe:
            # engine BOM rows are (rm_code, qty, scrap, yield)
            try:
                rm_code, qty, scrap, yld = entry
            except (ValueError, TypeError):
                continue
            qty, scrap, yld = _num(qty), _num(scrap), _num(yld)
            gross = round(soc_qty * qty * (1 + scrap) / (yld if yld > 0 else 1.0), 1)
            rm = rms.get(rm_code, {})
            stock = round(_num(rm.get("on_hand")), 1)
            net = round(max(0.0, gross - stock), 1)
            comps.append({
                "rm_code": rm_code, "rm_desc": rm.get("name", rm_code),
                "qty_per_unit": round(qty, 4), "gross": gross,
                "main_stock": stock, "substitute_stock": 0.0,
                "available": stock, "net_to_buy": net, "substitutes": [],
            })
            if is_adhoc:
                a = cons.setdefault(rm_code, {
                    "rm_code": rm_code, "rm_desc": rm.get("name", rm_code),
                    "gross": 0.0, "stock": stock, "items": set()})
                a["gross"] += gross
                a["stock"] = stock
                a["items"].add(sku.get("name", sku_id))

        products.append({
            "name": sku.get("name", sku_id), "soc_qty": soc_qty,
            "soc_count": 1, "is_adhoc": is_adhoc, "has_bom": has_bom,
            "components": comps,
            "net_total": round(sum(c["net_to_buy"] for c in comps), 1),
        })

    consolidated = []
    for code, a in cons.items():
        gross = round(a["gross"], 1)
        stock = round(a["stock"], 1)
        consolidated.append({
            "rm_code": code, "rm_desc": a["rm_desc"], "gross": gross,
            "main_stock": stock, "substitute_stock": 0.0, "available": stock,
            "net_to_buy": round(max(0.0, gross - stock), 1),
            "item_count": len(a["items"]), "items": sorted(a["items"])[:20],
        })
    consolidated.sort(key=lambda x: -x["net_to_buy"])

    products.sort(key=lambda x: (not x["is_adhoc"], -x["soc_qty"]))
    adhoc = [p for p in products if p["is_adhoc"]]
    return {
        "window_days": int(os.getenv("ADHOC_WINDOW_DAYS", "60")),
        "decode_names": decode,
        "products": products,
        "consolidated_rm": consolidated,
        "summary": {
            "soc_items": len(products),
            "adhoc_items": len(adhoc),
            "adhoc_with_bom": sum(1 for p in adhoc if p["has_bom"]),
            "consolidated_rms": len(consolidated),
            "rms_to_buy": sum(1 for x in consolidated if x["net_to_buy"] > 0),
            "total_buy_qty": round(sum(x["net_to_buy"] for x in consolidated), 1),
            "adhoc_soc_qty": round(sum(p["soc_qty"] for p in adhoc), 1),
            "source": data.get("_source", "synthetic") + " (pending SOC)",
        },
    }
