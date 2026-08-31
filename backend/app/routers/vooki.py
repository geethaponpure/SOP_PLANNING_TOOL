"""Vooki planning + the admin FG name->SKU mapping and added-SKU management."""
from ._deps import *

router = APIRouter()


@router.get("/api/vooki-planning")
def get_vooki_planning():
    return _vooki_planning()


@router.post("/api/vooki-planning/export")
def export_vooki_planning(body: VookiQty):
    name = "Vooki_Planning.xlsx"
    if body.product:
        safe = "".join(ch if (ch.isalnum() or ch in " -_") else "" for ch in body.product)[:40].strip()
        name = f"Vooki_{safe.replace(' ', '_') or 'FG'}.xlsx"
    return _xlsx(_pub.build_vooki_workbook(_vooki_planning(), body.quantities, _live_cycle(),
                                           product=body.product,
                                           stock_rows=(_crm_stock() if _live() else None),
                                           intransit_lots=(_intransit_lots_audit() if _live() else None)), name)


@router.get("/api/vooki-fg-map")
def get_vooki_fg_map_api():
    st = _mysql.status()
    vp = _vooki_planning()
    mapping = _mysql.get_vooki_fg_map()   # live sku_code -> product_name (admin view)
    return {
        "ready": st["ready"], "error": st["error"],
        "products": [p["name"] for p in vp.get("products", [])],
        "skus": [{**sku, "product": mapping.get(sku["code"], "")}
                 for sku in vp.get("fg_skus", [])],
        "mapped": len(mapping),
        "setup_hint": _mysql.SETUP_HINT,
    }


@router.post("/api/vooki-fg-map")
def set_vooki_fg_map_api(body: FgMapRow):
    res = _mysql.set_vooki_fg_map(body.sku_code, body.product_name)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Save failed")
    _vooki_fg_map.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True}


@router.post("/api/vooki-fg-map/bulk")
def bulk_vooki_fg_map_api(body: FgMapBulk):
    res = _mysql.bulk_set_vooki_fg_map([r.model_dump() for r in body.rows])
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Save failed")
    _vooki_fg_map.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True, "written": res["written"]}


@router.get("/api/vooki-fg-skus")
def get_vooki_fg_skus_api():
    st = _mysql.status()
    added = _added_fg_skus()
    added_codes = {a["sku_code"] for a in added}
    candidates = [{**c, "added": c["code"] in added_codes} for c in _vooki_division_items()]
    return {"ready": st["ready"], "error": st["error"], "setup_hint": _mysql.SETUP_HINT,
            "added": added, "candidates": candidates}


@router.post("/api/vooki-fg-skus")
def add_vooki_fg_sku_api(body: FgSkuRow):
    res = _mysql.add_fg_sku(body.sku_code, body.item_desc)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Add failed")
    _added_fg_skus.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True}


@router.post("/api/vooki-fg-skus/remove")
def remove_vooki_fg_sku_api(body: FgSkuRow):
    res = _mysql.remove_fg_sku(body.sku_code)
    if not res["ok"]:
        raise HTTPException(400, res["error"] or "Remove failed")
    _added_fg_skus.cache_clear()
    _vooki_planning.cache_clear()
    return {"ok": True}
