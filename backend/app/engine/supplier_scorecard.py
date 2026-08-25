"""Supplier criticality & scorecard engine (enhanced).

Scores every RM supplier from PO-receipt history on:
  OTIF · OTD · Fill rate · Lead-time consistency · Price vs market

CRITICALITY dimensions (new in this version):
  1. High lead time   (≥ 30 days)
  2. Import supplier  (currency ≠ INR or location outside India)
  3. Single dependency  (sole/only supplier for ≥ 1 RM item)
  4. Critical RM      (RM master criticality ≥ 0.7)
  5. Low on-time rate (<50 %)

Criticality rating → High / Medium / None
"""
from __future__ import annotations

import os
import statistics
from collections import defaultdict
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────

def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _pick(row: dict, cols: list[str]):
    for c in cols:
        if c in row:
            return row[c]
    return None


_ITEM_COLS   = ["Item_Code", "ItemCode", "item_code", "Itemcode", "Material", "Code"]
_NAME_COLS   = ["Item_Name", "ItemName", "Description", "item_description"]
_DATE_COLS   = ["GRN_Date", "receipt_date", "PO_Date", "ReceiptDate", "Date", "trx_date"]
_QTY_ORD_COLS = ["Ordered_Qty", "ordered_qty", "PO_Qty", "OrderQty"]
_QTY_RCV_COLS = ["Received_Qty", "received_qty", "GRN_Qty", "Quantity", "Qty"]
_PRICE_COLS  = ["Unit_Price", "unit_price", "Price", "Rate", "UnitCost", "BasicPrice"]
_VENDOR_COLS = ["Vendor", "vendor_name", "Supplier", "SupplierName", "VendorName"]
_CURR_COLS   = ["Currency", "currency", "Curr"]
_LOC_COLS    = ["Location", "City", "State", "Country", "vendor_location"]
_LEAD_COLS   = ["Lead_Time", "lead_time", "LeadTime", "actual_lead_days"]


def _load_po_receipts(warnings: list[str]) -> list[dict]:
    root = Path(__file__).resolve().parents[4]
    data_dir = os.getenv("DATA_DIR", "").strip() or str(root / "Data_Ingestion")
    search_dirs = [
        os.getenv("PO_RECEIPTS_DIR", ""),
        os.path.join(data_dir, "PO_receipts"),
        data_dir,
        str(root / "PO_receipts"),
        r"z:\PO_receipts",
        r"\\10.1.0.17\PPCAIProjects\PO_receipts",
        str(root),
    ]
    po_files: list[str] = []
    seen: set[str] = set()
    for d in search_dirs:
        if not d:
            continue
        try:
            p = Path(d)
            if not p.is_dir():
                continue
            for pat in ("Fy-*.xlsx", "FY-*.xlsx", "*PO_Receipts*.xlsx", "*PO_Receipts*.csv"):
                for f in sorted(p.glob(pat)):
                    key = f.name.lower()
                    if key not in seen:
                        seen.add(key)
                        po_files.append(str(f))
        except OSError:
            continue

    if not po_files:
        csv = os.getenv("PO_CSV", "")
        if not csv:
            for pat in ("Pure_PO_Receipts*.csv", "Pure_PO_Receipts*.xls*"):
                for f in sorted(root.glob(pat)):
                    csv = str(f)
                    break
        if csv:
            po_files = [csv]

    rows: list[dict] = []
    for fpath in po_files:
        try:
            from ..integration import po_file as _po
            rows.extend(_po.load_po_receipts(fpath))
        except Exception as e:   # noqa: BLE001
            warnings.append(f"po_file {Path(fpath).name}: {type(e).__name__}: {str(e)[:120]}")
    return rows


# ── criticality scorer ────────────────────────────────────────────────────────

def _assess_criticality(
    vendor: str,
    items_supplied: list[dict],
    avg_lead: float | None,
    on_time_rate: float,
    currencies: list[str],
    locations: list[str],
    sole_source_count: int,
    rm_master: dict,
) -> dict:
    reasons: list[str] = []
    score_factors: list[str] = []

    # 1. High lead time
    if avg_lead is not None and avg_lead >= 30:
        reasons.append(f"High lead time ({avg_lead:.0f}d)")
        score_factors.append("high_lead_time")

    # 2. Import / foreign currency
    non_inr = [c for c in currencies if c.upper() not in ("INR", "RS", "RS.", "")]
    if non_inr:
        reasons.append(f"Import ({', '.join(non_inr)})")
        score_factors.append("import")

    # 3. Single dependency
    if sole_source_count > 0:
        reasons.append(f"Sole source for {sole_source_count} RM(s)")
        score_factors.append("sole_source")

    # 4. Critical RM
    crit_rms = [it for it in items_supplied
                if _num(rm_master.get(it["code"], {}).get("criticality", 0)) >= 0.7]
    if crit_rms:
        reasons.append(f"Supplies critical RMs ({len(crit_rms)})")
        score_factors.append("critical_rm")

    # 5. Low reliability
    if on_time_rate < 0.5:
        reasons.append(f"Low on-time rate ({on_time_rate:.0%})")
        score_factors.append("low_reliability")

    n = len(score_factors)
    criticality = "High" if n >= 2 else ("Medium" if n == 1 else "None")
    is_critical  = n >= 1

    return {
        "criticality":         criticality,
        "critical":            is_critical,
        "criticality_reasons": reasons,
        "criticality_factors": score_factors,
    }


# ── main ──────────────────────────────────────────────────────────────────────

WEIGHTS = {"otif": 0.30, "otd": 0.25, "fill": 0.20, "lead_consistency": 0.10, "price": 0.15}
SCORE_LABEL = "OTIF 30% · OTD 25% · Fill 20% · Lead consistency 10% · Price 15%"


def build_supplier_scorecard(data: dict) -> dict:
    warnings: list[str] = []
    source = os.getenv("DATA_SOURCE", "synthetic").lower()
    rm_master = data.get("rms", {})

    if source == "live":
        po_rows = _load_po_receipts(warnings)
        if not po_rows:
            po_rows = _synthetic_po_scorecard(data)
            warnings.append("Falling back to synthetic PO data for supplier scorecard.")
    else:
        po_rows = _synthetic_po_scorecard(data)

    if not po_rows:
        return {"note": "No PO-receipts data. Scorecard requires PO history."}

    # ── group rows by vendor ──────────────────────────────────────────────────
    # vendor → item → list of receipts
    vendor_items: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for r in po_rows:
        vendor = str(_pick(r, _VENDOR_COLS) or "Unknown").strip()
        code   = str(_pick(r, _ITEM_COLS) or "").strip()
        if not vendor or not code:
            continue
        name   = str(_pick(r, _NAME_COLS) or code)
        qty_r  = _num(_pick(r, _QTY_RCV_COLS))
        qty_o  = _num(_pick(r, _QTY_ORD_COLS)) or qty_r   # fallback = received
        price  = _num(_pick(r, _PRICE_COLS))
        curr   = str(_pick(r, _CURR_COLS) or "INR").strip()
        loc    = str(_pick(r, _LOC_COLS) or "").strip()
        lead   = _num(_pick(r, _LEAD_COLS))
        date_v = _pick(r, _DATE_COLS)

        vendor_items[vendor][code].append({
            "name": name, "qty_r": qty_r, "qty_o": qty_o,
            "price": price, "currency": curr, "location": loc,
            "lead": lead, "date": date_v,
        })

    # ── item-level market price (median across all suppliers) ─────────────────
    item_prices: dict[str, list[float]] = defaultdict(list)
    for vendor, items in vendor_items.items():
        for code, receipts in items.items():
            prices = [r["price"] for r in receipts if r["price"] > 0]
            if prices:
                item_prices[code].append(sum(prices) / len(prices))
    market_price: dict[str, float] = {
        c: round(statistics.median(ps), 4) for c, ps in item_prices.items() if ps
    }

    # ── item-level lead-time benchmark (median per item) ──────────────────────
    item_leads: dict[str, list[float]] = defaultdict(list)
    for vendor, items in vendor_items.items():
        for code, receipts in items.items():
            leads = [r["lead"] for r in receipts if r["lead"] > 0]
            if leads:
                item_leads[code].extend(leads)
    median_lead: dict[str, float] = {
        c: statistics.median(ls) for c, ls in item_leads.items() if ls
    }

    # ── who is sole-supplier per item? ────────────────────────────────────────
    item_vendors: dict[str, set[str]] = defaultdict(set)
    for vendor, items in vendor_items.items():
        for code in items:
            item_vendors[code].add(vendor)
    sole_source_items_by_vendor: dict[str, list[str]] = defaultdict(list)
    for code, vendors in item_vendors.items():
        if len(vendors) == 1:
            v = next(iter(vendors))
            sole_source_items_by_vendor[v].append(code)

    # ── build supplier rows ───────────────────────────────────────────────────
    suppliers = []
    for vendor, items_dict in vendor_items.items():
        otif_list, otd_list, fill_list, lead_list, price_list = [], [], [], [], []
        currencies: set[str] = set()
        locations:  set[str] = set()
        total_spend = 0.0
        items_detail: list[dict] = []
        po_lines = 0

        for code, receipts in items_dict.items():
            rm_info = rm_master.get(code, {})
            item_name = receipts[0]["name"]
            med_lead_item = median_lead.get(code, rm_info.get("lead_time_days", 14) or 14)
            lead_tol = med_lead_item * 1.25   # 25% tolerance

            item_otif, item_otd, item_fill = [], [], []
            item_leads_v, item_prices_v = [], []
            item_spend = 0.0
            item_recv  = 0.0

            for rec in receipts:
                po_lines += 1
                currencies.add(rec["currency"] or "INR")
                if rec["location"]:
                    locations.add(rec["location"])
                qty_r = rec["qty_r"]
                qty_o = rec["qty_o"]
                price = rec["price"]
                lead  = rec["lead"]

                fill = min(qty_r / qty_o, 1.0) if qty_o > 0 else 1.0
                on_time = lead <= lead_tol if lead > 0 else True
                full    = fill >= 0.95

                item_otif.append(1.0 if on_time and full else 0.0)
                item_otd.append(1.0 if on_time else 0.0)
                item_fill.append(fill)
                if lead > 0:
                    item_leads_v.append(lead)
                if price > 0:
                    item_prices_v.append(price)
                item_spend += qty_r * price
                item_recv  += qty_r

            total_spend += item_spend
            avg_item_price = item_spend / item_recv if item_recv > 0 else None
            mkt            = market_price.get(code)
            pvmkt          = round((avg_item_price / mkt - 1) * 100, 1) if avg_item_price and mkt else None

            otif_list.extend(item_otif)
            otd_list.extend(item_otd)
            fill_list.extend(item_fill)
            lead_list.extend(item_leads_v)
            price_list.extend(item_prices_v)

            items_detail.append({
                "code":          code,
                "name":          item_name,
                "lines":         len(receipts),
                "received":      round(item_recv, 1),
                "avg_price":     round(avg_item_price, 4) if avg_item_price else None,
                "market_price":  mkt,
                "price_vs_market": pvmkt,
                "avg_lead":      round(statistics.mean(item_leads_v), 1) if item_leads_v else None,
                "spend":         round(item_spend, 0),
            })

        # aggregate metrics
        otif = round(statistics.mean(otif_list) * 100, 1) if otif_list else 0.0
        otd  = round(statistics.mean(otd_list)  * 100, 1) if otd_list else 0.0
        fill = round(statistics.mean(fill_list) * 100, 1) if fill_list else 0.0
        avg_lead = round(statistics.mean(lead_list), 1) if lead_list else None

        # lead consistency (lower CV = better; map to 0-100)
        if len(lead_list) >= 2:
            cv = statistics.stdev(lead_list) / statistics.mean(lead_list)
            lead_consist = round(max(0.0, 100 - cv * 100), 1)
        else:
            lead_consist = 80.0

        # price vs market
        all_item_prices = price_list
        if all_item_prices:
            vendor_avg_price = statistics.mean(all_item_prices)
            # market = median across all
            mkt_avg = statistics.median([v for vs in item_prices.values() for v in vs]) if item_prices else vendor_avg_price
            price_score = round(max(0.0, 100 - max(0, vendor_avg_price / mkt_avg - 1) * 200), 1)
            price_vs_market = round((vendor_avg_price / mkt_avg - 1) * 100, 1)
        else:
            price_score = 50.0
            price_vs_market = None

        # weighted score
        score = round(
            WEIGHTS["otif"]            * otif +
            WEIGHTS["otd"]             * otd  +
            WEIGHTS["fill"]            * fill +
            WEIGHTS["lead_consistency"] * lead_consist +
            WEIGHTS["price"]           * price_score,
            1,
        )

        # trade type
        non_inr = [c for c in currencies if c.upper() not in ("INR", "RS", "RS.", "")]
        trade = "Import" if non_inr else "Domestic"

        # sole-source items
        sole_items = sole_source_items_by_vendor.get(vendor, [])
        sole_objs  = [{"code": c, "name": items_dict.get(c, [{}])[0].get("name", c) if items_dict.get(c) else c}
                      for c in sole_items]

        # criticality
        crit_info = _assess_criticality(
            vendor=vendor,
            items_supplied=[{"code": c} for c in items_dict],
            avg_lead=avg_lead,
            on_time_rate=(otd / 100.0),
            currencies=list(currencies),
            locations=list(locations),
            sole_source_count=len(sole_items),
            rm_master=rm_master,
        )

        # sort item detail by spend descending
        items_detail.sort(key=lambda i: -(i["spend"] or 0))

        suppliers.append({
            "vendor":            vendor,
            "score":             score,
            "otif":              otif,
            "otd":               otd,
            "fill_rate":         fill,
            "lead_consistency":  lead_consist,
            "avg_lead_time":     avg_lead,
            "price_vs_market":   price_vs_market,
            "po_lines":          po_lines,
            "item_count":        len(items_dict),
            "spend":             round(total_spend, 0),
            "currencies":        sorted(currencies - {"", "INR"}) or ["INR"],
            "locations":         sorted(locations)[:3],
            "trade":             trade,
            "sole_source_count": len(sole_items),
            "sole_source_items": sole_objs,
            "items":             items_detail[:20],
            **crit_info,
        })

    suppliers.sort(key=lambda s: -s["score"])

    critical_count  = sum(1 for s in suppliers if s["critical"])
    sole_src_count  = sum(1 for s in suppliers if s["sole_source_count"] > 0)
    avg_score = round(statistics.mean([s["score"] for s in suppliers]), 1) if suppliers else 0.0

    return {
        "suppliers":  suppliers,
        "_warnings":  warnings,
        "summary": {
            "suppliers":      len(suppliers),
            "items_supplied": len({c for v in vendor_items.values() for c in v}),
            "critical":       critical_count,
            "sole_source":    sole_src_count,
            "avg_score":      avg_score,
            "weights":        SCORE_LABEL,
        },
    }


# ── synthetic PO for scorecard ────────────────────────────────────────────────

def _synthetic_po_scorecard(data: dict) -> list[dict]:
    import random
    from datetime import date, timedelta

    rng   = random.Random(20260616)
    rows  = []
    start = date(2024, 4, 1)
    rms   = data.get("rms", {})

    VENDORS = [
        ("BASF India", "INR", "Mumbai"),
        ("Huntsman Chemicals", "USD", "Singapore"),
        ("Jubilant Life Sciences", "INR", "Noida"),
        ("Merck KGaA India", "EUR", "Bengaluru"),
        ("Apcotex Industries", "INR", "Taloja"),
    ]

    for rm_code, rm in rms.items():
        n_vendors = max(1, rm.get("suppliers", 1))
        vendors_for_rm = rng.sample(VENDORS, min(n_vendors, len(VENDORS)))
        base_price = _num(rm.get("unit_cost", 1.0)) or 1.0
        lead = rm.get("lead_time_days", 14)
        lvar = rm.get("lead_time_variability", 0.2)
        moq  = _num(rm.get("moq", 500))

        for vendor_name, currency, location in vendors_for_rm:
            for month in range(24):
                if rng.random() < 0.3:
                    continue   # not every vendor supplies every month
                d    = start + timedelta(days=month * 28 + rng.randint(0, 8))
                price_mult = rng.uniform(0.85, 1.18)
                if currency != "INR":
                    price_mult *= rng.uniform(1.05, 1.20)  # import premium
                price = round(base_price * price_mult, 4)
                qty_o = round(moq * rng.uniform(0.8, 2.0), 1)
                qty_r = round(qty_o * rng.uniform(0.90, 1.0), 1) if rng.random() > 0.1 else round(qty_o * rng.uniform(0.5, 0.89), 1)
                actual_lead = max(1.0, round(lead * rng.gauss(1.0, lvar), 1))
                rows.append({
                    "Item_Code":    rm_code,
                    "Item_Name":    rm.get("name", rm_code),
                    "GRN_Date":     d,
                    "Ordered_Qty":  qty_o,
                    "Received_Qty": qty_r,
                    "Unit_Price":   price,
                    "Vendor":       vendor_name,
                    "Currency":     currency,
                    "Location":     location,
                    "Lead_Time":    actual_lead,
                })
    return rows
