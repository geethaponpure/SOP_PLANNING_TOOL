"""CRM (SQL Server) source queries -- the exact SPs / functions provided by
the client. Each function returns a list of dict rows.

Report SPs deny EXECUTE to the read-only login, so where needed we replicate
their logic with a plain SELECT over the same base tables / table functions.
"""
from __future__ import annotations

import os

from . import db

# PTO/PTS + item master (columns confirmed from the client query).
PTO_PTS_SQL = """
select p.*, isnull(pt.Actiontypes, 'PTO') as Itemtype
from (
    select distinct im.item_id as ItemId, im.item_description as Item_Name,
           im.item_code as Item_Code, im.uom as UOM,
           ic.segment1 as Segment1, ic.segment2 as Segment2,
           ic.segment3 as Segment3, ic.segment4 as Segment4
    from itemmasters im
    join ItemCategories ic on im.item_id = ic.item_id
    where ic.segment1 = 'Performance Chemicals'
      and im.enabled_flag = 'Y' and im.status = 'Active'
      {division_filter}
) p
left outer join (
    select * from PurchaseRequisitionPtoPts
    where cast(getdate() as date) between fromdate and todate
) pt on p.ItemId = pt.itemid
"""


def pto_pts() -> list[dict]:
    """SKU master + PTO/PTS classification (Performance Chemicals)."""
    division = os.getenv("CRM_DIVISION", "").strip()
    if division:
        return db.crm_query(PTO_PTS_SQL.format(division_filter="and ic.segment2 = ?"), (division,))
    return db.crm_query(PTO_PTS_SQL.format(division_filter=""))


# Open-PO in-transit — live balance (ordered − received − cancelled) per item code,
# scoped to recent POs and below a blanket-PO size, from Oracle's BiPoDetails.
PO_INTRANSIT_SQL = """
SELECT p.item_code AS Item_Code, MAX(im.item_description) AS Item_Desc,
       p.vendor_name AS Vendor_Name,
       SUM(p.quantity - p.quantity_received - p.quantity_cancelled) AS InTransit,
       SUM(p.quantity_received) AS Received, COUNT(*) AS OpenLines
FROM BiPoDetails p
LEFT JOIN itemmasters im ON im.item_code = p.item_code
WHERE (p.quantity - p.quantity_received - p.quantity_cancelled) > 0
  AND p.po_date >= ? AND p.quantity <= ?
GROUP BY p.item_code, p.vendor_name
"""


def po_open_intransit(from_date, blanket_qty) -> list[dict]:
    """Open-PO in-transit rows (item_code, item_desc, vendor, balance, received) for
    POs placed on/after ``from_date`` with an ordered qty <= ``blanket_qty`` (excludes
    blanket/framework contracts). Grouped by (item_code, vendor) so the caller can drop
    inter-company vendors before rolling up."""
    return db.crm_query(PO_INTRANSIT_SQL, (str(from_date), float(blanket_qty or 0) or 1e12))


# Per-PO-line detail behind the in-transit total — for the validation/audit sheet.
PO_INTRANSIT_DETAIL_SQL = """
SELECT p.item_code AS Item_Code, im.item_description AS Item_Desc, p.po_number AS Po_Number,
       p.po_date AS Po_Date, p.vendor_name AS Vendor_Name, p.inv_org_name AS Org_Name,
       p.procurement_type AS Procurement_Type,
       p.quantity AS Quantity, p.quantity_received AS Received, p.quantity_cancelled AS Cancelled,
       (p.quantity - p.quantity_received - p.quantity_cancelled) AS InTransit
FROM BiPoDetails p
LEFT JOIN itemmasters im ON im.item_code = p.item_code
WHERE (p.quantity - p.quantity_received - p.quantity_cancelled) > 0
  AND p.po_date >= ? AND p.quantity <= ?
"""


def po_open_intransit_detail(from_date, blanket_qty) -> list[dict]:
    """One row per open PO line behind the in-transit total (PO#, vendor, ordered,
    received, cancelled, balance) — the audit trail for validation."""
    return db.crm_query(PO_INTRANSIT_DETAIL_SQL, (str(from_date), float(blanket_qty or 0) or 1e12))


# Pending SOC -- read-only replication of SP_SOCSummaryReport.
SOC_SQL = """
SELECT im.item_code AS Item_Code,
       SUM(sod.quantity - ISNULL(d.disp_qty, 0)) AS PendingQty
FROM SaleOrderHdrs soh
JOIN SaleOrderdtls sod ON sod.header_id = soh.header_id
JOIN ItemMasters im ON im.item_id = sod.item_id
LEFT JOIN (SELECT sale_order_detail_line_id AS line_id, SUM(sale_quantity) AS disp_qty
           FROM dispatchdetails GROUP BY sale_order_detail_line_id) d
       ON d.line_id = sod.line_id
WHERE sod.status = 'OPEN'
  AND soh.creation_date >= DATEADD(month, -{months}, GETDATE())
  AND (sod.quantity - ISNULL(d.disp_qty, 0)) > 0
GROUP BY im.item_code
HAVING SUM(sod.quantity - ISNULL(d.disp_qty, 0)) > 0
"""


def soc_pending() -> list[dict]:
    months = int(os.getenv("SOC_WINDOW_MONTHS", "6"))
    return db.crm_query(SOC_SQL.format(months=months))


CUSTOMER_CLASS_SQL = """
SELECT cm.customer_number AS CustomerNumber, ccd.Classification AS Class,
       ccd.SubClass AS SubClass, ccd.Account_Year AS AccYear
FROM CustomerClassificationHeaders cch
JOIN CustomerClassificationDetails ccd ON ccd.Header_Id = cch.Header_Id
JOIN CustomerMasters cm ON cm.customer_id = cch.Customer_Id
"""


def customer_classification() -> list[dict]:
    return db.crm_query(CUSTOMER_CLASS_SQL)


QUOTE_SQL = """
select item_code, quotation_date, quantity
from FnQuotationDetails()
where quotation_date >= dateadd(month, -{months}, getdate())
"""


def quote_details() -> list[dict]:
    months = int(os.getenv("QUOTE_WINDOW_MONTHS", "3"))
    return db.crm_query(QUOTE_SQL.format(months=months))


DISPATCH_SQL = """
select Itemcode, trx_date, sale_quantity, unit_price, uom
from FnDespatchDetails()
where trx_date >= dateadd(month, -{months}, getdate())
"""


def dispatch_details() -> list[dict]:
    months = int(os.getenv("HISTORY_MONTHS", "24")) + 2
    return db.crm_query(DISPATCH_SQL.format(months=months))


def business_plan() -> list[dict]:
    raise NotImplementedError("Use business_plan_projection() — the JC-wise report replication.")


# ── Approved projection — read-only replication of ────────────────────────────
#    SP_SCBusinessPlan_GetDetailedReportJCWise (EXECUTE denied to the report login).
# The SP, for a JC "n", reports per item:
#   Current  = jc{n}_week1_user_dfn_qty + jc{n}_week2_user_dfn_qty   (from …Dtls)
#   Next1 JC = jc_nextmonth1_qty, Next2 JC = jc_nextmonth2_qty       (from …JCDtls)
# …JCDtls is keyed by the JourneyCalendar row (jc_type = JourneyCalendars.line_id
# whose name='JCn' for that acc_year), joined on the detail line (…JCDtls.header_id
# = …Dtls.line_id). Aggregated per item_description across all customers/collectors.
# Only APPROVED plan rows count: h.jc{n}_status = 5 (1 Pending, 2 Waiting-TE,
# 3 Waiting-BM, 4 Waiting-PM/RM, 5 Approved).
APPROVED_JC_STATUS = 5

BUSINESS_PLAN_PROJECTION_SQL = """
SELECT h.item_description AS ItemName,
       MAX(h.segment2) AS Segment2, MAX(h.segment3) AS Segment3,
       SUM(ISNULL(d.jc{n}_week1_user_dfn_qty, 0)
         + ISNULL(d.jc{n}_week2_user_dfn_qty, 0)) AS CurrentQ,
       SUM(ISNULL(j.jc_nextmonth1_qty, 0)) AS Next1Q,
       SUM(ISNULL(j.jc_nextmonth2_qty, 0)) AS Next2Q
FROM SCBusinessMonthlyPlanHdrs h
JOIN SCBusinessMonthlyPlanDtls d ON d.header_id = h.header_id
LEFT JOIN SCBusinessMonthlyPlanJCDtls j
       ON j.header_id = d.line_id
      AND j.jc_type = (SELECT TOP 1 line_id FROM JourneyCalendars
                       WHERE acc_year = h.acc_year AND name = 'JC{n}' AND is_active = 1)
WHERE h.acc_year = ?
  {status_filter}
GROUP BY h.item_description
"""


def _status_filter(n: int, approved_only: bool) -> str:
    return f"AND h.jc{n}_status = {APPROVED_JC_STATUS}" if approved_only else ""


def business_plan_projection(acc_year: str, jc_number: int,
                             approved_only: bool = True) -> list[dict]:
    """Projection for a JC: Current (WK1+WK2), Next1 JC, Next2 JC, per item.
    approved_only keeps only rows whose JC{n} status = Approved (5)."""
    n = int(jc_number)
    if not 1 <= n <= 13:
        raise ValueError(f"jc_number out of range (1-13): {jc_number}")
    return db.crm_query(
        BUSINESS_PLAN_PROJECTION_SQL.format(n=n, status_filter=_status_filter(n, approved_only)),
        (acc_year,))


BUSINESS_PLAN_PROJECTION_ROWS_SQL = """
SELECT h.item_description AS ItemName,
       CAST(ISNULL(d.collector, '') AS nvarchar(400)) AS Collector,
       MAX(h.segment2) AS Segment2, MAX(h.segment3) AS Segment3,
       SUM(ISNULL(d.jc{n}_week1_user_dfn_qty, 0)
         + ISNULL(d.jc{n}_week2_user_dfn_qty, 0)) AS CurrentQ,
       SUM(ISNULL(j.jc_nextmonth1_qty, 0)) AS Next1Q,
       SUM(ISNULL(j.jc_nextmonth2_qty, 0)) AS Next2Q
FROM SCBusinessMonthlyPlanHdrs h
JOIN SCBusinessMonthlyPlanDtls d ON d.header_id = h.header_id
LEFT JOIN SCBusinessMonthlyPlanJCDtls j
       ON j.header_id = d.line_id
      AND j.jc_type = (SELECT TOP 1 line_id FROM JourneyCalendars
                       WHERE acc_year = h.acc_year AND name = 'JC{n}' AND is_active = 1)
WHERE h.acc_year = ?
  {status_filter}
GROUP BY h.item_description, CAST(ISNULL(d.collector, '') AS nvarchar(400))
"""


def business_plan_projection_rows(acc_year: str, jc_number: int,
                                  approved_only: bool = True) -> list[dict]:
    """Projection per (item, collector) — for Projection vs Sales (keeps collector)."""
    n = int(jc_number)
    if not 1 <= n <= 13:
        raise ValueError(f"jc_number out of range (1-13): {jc_number}")
    return db.crm_query(
        BUSINESS_PLAN_PROJECTION_ROWS_SQL.format(n=n, status_filter=_status_filter(n, approved_only)),
        (acc_year,))


def journey_calendar(acc_year: str | None = None) -> list[dict]:
    """The authoritative JC calendar from CRM (name, dates, active/closed)."""
    if acc_year:
        return db.crm_query(
            "SELECT line_id, name, effective_from, effective_to, is_active, is_closed, acc_year "
            "FROM JourneyCalendars WHERE acc_year = ? ORDER BY effective_from", (acc_year,))
    return db.crm_query(
        "SELECT line_id, name, effective_from, effective_to, is_active, is_closed, acc_year "
        "FROM JourneyCalendars ORDER BY acc_year, effective_from")


# ── Stock -- read-only equivalent of SPBiStockDetails (latest BiStockDetail) ──
STOCK_DETAILS_SQL = """
SELECT ORGANIZATION_NAME AS Organization, ITEM_CODE AS ItemCode,
       MAX([DESCRIPTION]) AS ItemDesc, SUBINVENTORY_CODE AS SubInv,
       SUM(OPENING_QTY) AS Qty, MAX(ITEM_COST) AS ItemCost
FROM BiStockDetail
WHERE TRANS_DATE = (SELECT MAX(TRANS_DATE) FROM BiStockDetail)
GROUP BY ORGANIZATION_NAME, ITEM_CODE, SUBINVENTORY_CODE
"""


def stock_details() -> list[dict]:
    """Latest on-hand stock per item / org / sub-inventory (from BiStockDetail)."""
    return db.crm_query(STOCK_DETAILS_SQL)


STOCK_LOTS_SQL = """
DECLARE @d date = (SELECT MAX(TRANS_DATE) FROM BiStockDetail);
SELECT ORGANIZATION_NAME AS Organization, INV_ORG_CODE AS OrgCode, ITEM_CODE AS ItemCode,
       MAX([DESCRIPTION]) AS ItemDesc, SUBINVENTORY_CODE AS SubInv,
       ISNULL(LOT_NUMBER, '') AS Lot, SUM(OPENING_QTY) AS Qty,
       MAX(AGING_DATE) AS AgingDate, MAX(DATEDIFF(day, AGING_DATE, @d)) AS AgeDays
FROM BiStockDetail
WHERE TRANS_DATE = @d
GROUP BY ORGANIZATION_NAME, INV_ORG_CODE, ITEM_CODE, SUBINVENTORY_CODE, ISNULL(LOT_NUMBER, '')
"""


def stock_lots() -> list[dict]:
    """Lot-wise on-hand stock per item / org / sub-inventory / lot (audit trail)."""
    return db.crm_query(STOCK_LOTS_SQL)


STOCK_AGED_SQL = """
DECLARE @d date = (SELECT MAX(TRANS_DATE) FROM BiStockDetail);
SELECT ORGANIZATION_NAME AS Organization, ITEM_CODE AS ItemCode,
       MAX([DESCRIPTION]) AS ItemDesc, SUBINVENTORY_CODE AS SubInv,
       SUM(OPENING_QTY) AS Qty, MAX(ITEM_COST) AS ItemCost,
       MAX(DATEDIFF(day, AGING_DATE, @d)) AS MaxAgeDays
FROM BiStockDetail
WHERE TRANS_DATE = @d
  AND DATEDIFF(day, AGING_DATE, @d) > ?
  AND OPENING_QTY > 0
GROUP BY ORGANIZATION_NAME, ITEM_CODE, SUBINVENTORY_CODE
"""


def stock_details_aged(min_age_days: int = 90) -> list[dict]:
    """Aged on-hand stock (older than ``min_age_days``) per item / org / sub-inv."""
    return db.crm_query(STOCK_AGED_SQL, (int(min_age_days),))


# Item 'Business' = ItemCategories.segment2 ('Raw Material' = RM).
ITEM_BUSINESS_SQL = """
SELECT im.item_code AS ItemCode, ic.segment2 AS Business
FROM ItemCategories ic
JOIN itemmasters im ON im.item_id = ic.item_id
WHERE ic.segment2 IS NOT NULL
"""


ITEM_SEGMENTS_SQL = """
SELECT im.item_code AS ItemCode,
       MAX(im.item_description) AS ItemName,
       MAX(CASE WHEN ic.segment1 IN ('Performance Chemicals','NPD') THEN ic.segment1 END) AS DivisionTarget,
       MAX(ic.segment1) AS Segment1,
       MAX(ic.segment2) AS Segment2, MAX(ic.segment3) AS Segment3,
       MAX(ic.segment4) AS Segment4
FROM ItemCategories ic
JOIN itemmasters im ON im.item_id = ic.item_id
WHERE ic.segment2 IS NOT NULL
GROUP BY im.item_code
"""


def item_segments() -> list[dict]:
    """Per item code: Segment1 (Division), Segment2 (Business), Segment3 (sub-category)
    and Segment4 (product family — the level Technical Head/Manager scopes use).
    DivisionTarget surfaces 'Performance Chemicals'/'NPD' even when the item also carries
    a non-target category (an item may sit under several divisions)."""
    return db.crm_query(ITEM_SEGMENTS_SQL)


# ── CRM users (dbo.Users) — for the app's User Master approval ─────────────────
# The planning tool is only for supply-chain / planning / manufacturing / warehouse /
# R&D staff, so the picker is filtered by dbo.Users.department (admin-configurable).
def crm_users(departments: list[str] | None = None, q: str | None = None,
              active_only: bool = True, limit: int = 5000) -> list[dict]:
    """Active CRM users, filtered to the allowed departments and an optional search
    over name / username / email / user_code."""
    where = []
    params: list = []
    if active_only:
        where.append("is_active = 1")
    depts = [d for d in (departments or []) if d and str(d).strip()]
    if depts:
        where.append("department IN (" + ", ".join(["?"] * len(depts)) + ")")
        params += depts
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append("(name LIKE ? OR username LIKE ? OR email LIKE ? OR user_code LIKE ?)")
        params += [like, like, like, like]
    sql = (f"SELECT TOP {int(limit)} line_id AS LineId, name AS Name, username AS Username, "
           "email AS Email, mobile_number AS Mobile, user_code AS UserCode, "
           "department AS Department, designation AS Designation "
           "FROM dbo.Users"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY name")
    return db.crm_query(sql, tuple(params))


def crm_items(q: str | None = None, limit: int = 50) -> list[dict]:
    """Search active items from CRM itemmasters — returns {name (item_description), code, uom}.
    Ordered by item_description; a search matches description OR code."""
    # itemmasters.is_active is NULL for every row; the real active flag is enabled_flag ('Y'/'N')
    where = ["ISNULL(enabled_flag, 'Y') <> 'N'", "item_description IS NOT NULL"]
    params: list = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append("(item_description LIKE ? OR item_code LIKE ?)")
        params += [like, like]
    sql = (f"SELECT TOP {int(limit)} item_description AS Name, item_code AS Code, uom AS Uom "
           "FROM itemmasters WHERE " + " AND ".join(where) + " ORDER BY item_description")
    return db.crm_query(sql, tuple(params))


def crm_user_departments() -> list[dict]:
    """Distinct active-user departments with counts — for the admin dept filter."""
    return db.crm_query(
        "SELECT department AS Department, COUNT(*) AS N FROM dbo.Users "
        "WHERE is_active = 1 AND department IS NOT NULL AND LTRIM(RTRIM(department)) <> '' "
        "GROUP BY department ORDER BY department")


def item_business() -> list[dict]:
    return db.crm_query(ITEM_BUSINESS_SQL)


# Vooki-division item master (code + description) — candidate FG SKUs the admin
# can add to the Vooki FG names list. Filtered by ItemCategories.segment2.
VOOKI_ITEMS_SQL = """
SELECT DISTINCT im.item_code AS ItemCode, im.item_description AS ItemDesc
FROM ItemCategories ic
JOIN itemmasters im ON im.item_id = ic.item_id
WHERE ic.segment2 = ?
  AND im.item_code IS NOT NULL AND im.item_description IS NOT NULL
"""


def vooki_division_items(business: str = "Vooki Division") -> list[dict]:
    return db.crm_query(VOOKI_ITEMS_SQL, (business,))


# Pending SOC (un-despatched balance) -- read-only replication of
# SpDespatchPendingReport over FnOrderDtlPending x FnScheduleDtlPending.
DESPATCH_PENDING_SQL = """
SELECT h.PRODUCTCODE AS ItemCode, h.PRODUCT AS ItemDesc,
       SUM(d.BALANCE_QTY) AS PendingQty
FROM FnOrderDtlPending() h
JOIN FnScheduleDtlPending() d
  ON h.line_id = d.SOC_LINE_ID AND h.id = d.ORDER_NO
WHERE CAST(d.scheduled_qty AS float) > 0
  AND CAST(d.BALANCE_QTY AS float) > 0
  AND CAST(h.soc_date AS date) BETWEEN ? AND ?
  AND (UPPER(ISNULL(h.SALES_TYPE, '')) <> 'BULK'
       OR (CAST(d.despatched_qty AS float) / NULLIF(CAST(d.scheduled_qty AS float), 0)) * 100 < 95)
GROUP BY h.PRODUCTCODE, h.PRODUCT
"""


def despatch_pending(from_date, to_date) -> list[dict]:
    return db.crm_query(DESPATCH_PENDING_SQL, (str(from_date), str(to_date)))


# MFG SOC pending -- same as above but only for the planning dispatch orgs
# (FnScheduleDtlPending.INVENTORY_ORG_NAME IN (...)). Used to compute the current JC.
def despatch_pending_mfg(from_date, to_date, orgs) -> list[dict]:
    orgs = [o for o in (orgs or []) if o]
    if not orgs:
        return []
    ph = ",".join("?" for _ in orgs)
    sql = DESPATCH_PENDING_SQL.replace(
        "GROUP BY h.PRODUCTCODE, h.PRODUCT",
        f"AND d.INVENTORY_ORG_NAME IN ({ph})\nGROUP BY h.PRODUCTCODE, h.PRODUCT")
    return db.crm_query(sql, (str(from_date), str(to_date), *orgs))


# MFG SOC pending per (product x collector) -- same filters as despatch_pending_mfg but
# KEEPS the sales collector, for the per-collector SOC sheets in the BU report.
DESPATCH_PENDING_ROWS_SQL = """
SELECT h.PRODUCTCODE AS ItemCode, h.PRODUCT AS ItemDesc,
       CAST(ISNULL(h.collector, '') AS nvarchar(400)) AS Collector,
       SUM(d.BALANCE_QTY) AS PendingQty
FROM FnOrderDtlPending() h
JOIN FnScheduleDtlPending() d
  ON h.line_id = d.SOC_LINE_ID AND h.id = d.ORDER_NO
WHERE CAST(d.scheduled_qty AS float) > 0
  AND CAST(d.BALANCE_QTY AS float) > 0
  AND CAST(h.soc_date AS date) BETWEEN ? AND ?
  AND (UPPER(ISNULL(h.SALES_TYPE, '')) <> 'BULK'
       OR (CAST(d.despatched_qty AS float) / NULLIF(CAST(d.scheduled_qty AS float), 0)) * 100 < 95)
  {org_filter}
GROUP BY h.PRODUCTCODE, h.PRODUCT, CAST(ISNULL(h.collector, '') AS nvarchar(400))
"""


def despatch_pending_mfg_rows(from_date, to_date, orgs) -> list[dict]:
    """MFG SOC pending per (product, collector) for the planning dispatch orgs only."""
    orgs = [o for o in (orgs or []) if o]
    if not orgs:
        return []
    ph = ",".join("?" for _ in orgs)
    sql = DESPATCH_PENDING_ROWS_SQL.format(org_filter=f"AND d.INVENTORY_ORG_NAME IN ({ph})")
    return db.crm_query(sql, (str(from_date), str(to_date), *orgs))


# Pending SOC split by SCHEDULE_DATE (for production scheduling: Pending vs Future
# SOC). Returns per item x schedule-date the un-despatched balance quantity.
SOC_SCHEDULE_SQL = """
SELECT h.PRODUCTCODE AS ItemCode, h.PRODUCT AS ItemDesc,
       CAST(d.SCHEDULE_DATE AS date) AS ScheduleDate,
       SUM(CAST(d.BALANCE_QTY AS float)) AS Qty
FROM FnOrderDtlPending() h
JOIN FnScheduleDtlPending() d ON h.line_id = d.SOC_LINE_ID AND h.id = d.ORDER_NO
WHERE CAST(d.scheduled_qty AS float) > 0 AND CAST(d.BALANCE_QTY AS float) > 0
GROUP BY h.PRODUCTCODE, h.PRODUCT, CAST(d.SCHEDULE_DATE AS date)
"""


def soc_schedule() -> list[dict]:
    """Open SOC balance per item and schedule date (SP_SOCDetailReport basis)."""
    return db.crm_query(SOC_SCHEDULE_SQL)


def dispatch_by_jc(jcs: list[dict]) -> list[dict]:
    """Dispatched qty per item x collector, split across the given JCs (SUM-CASE).
    Read-only replication of SP_DespatchDetailsReport over FnDespatchDetails()."""
    if not jcs:
        return []
    cases, params = [], []
    for i, jc in enumerate(jcs):
        cases.append(f"SUM(CASE WHEN trx_date BETWEEN ? AND ? "
                     f"THEN sale_quantity ELSE 0 END) AS jc{i}")
        params += [jc["from"], jc["to"]]
    params += [jcs[0]["from"], jcs[-1]["to"]]
    sql = f"""
        SELECT Itemcode AS ItemCode, MAX(ItemName) AS ItemName,
               Collector, collector_id AS CollectorId, {', '.join(cases)}
        FROM FnDespatchDetails()
        WHERE trx_date BETWEEN ? AND ?
        GROUP BY Itemcode, Collector, collector_id
    """
    return db.crm_query(sql, tuple(params))


def dispatch_scope(jcs: list[dict]) -> list[dict]:
    """Dispatch qty + value per item x customer x collector x market-circle,
    split across the given JCs (SUM-CASE, same shape as dispatch_by_jc). This is
    the permission-dashboard cube: every dimension a persona scope filters on
    (mc_code / collector / customer; item -> segment via item_segments)."""
    if not jcs:
        return []
    cases, params = [], []
    for i, jc in enumerate(jcs):
        cases.append(f"SUM(CASE WHEN trx_date BETWEEN ? AND ? "
                     f"THEN sale_quantity ELSE 0 END) AS jc{i}")
        cases.append(f"SUM(CASE WHEN trx_date BETWEEN ? AND ? "
                     f"THEN total_value ELSE 0 END) AS val{i}")
        params += [jc["from"], jc["to"], jc["from"], jc["to"]]
    params += [jcs[0]["from"], jcs[-1]["to"]]
    sql = f"""
        SELECT Itemcode AS ItemCode, MAX(ItemName) AS ItemName,
               customer_id AS CustomerId, MAX(CustomerName) AS CustomerName,
               collector_id AS CollectorId, MAX(Collector) AS Collector,
               mc_code AS McCode, {', '.join(cases)}
        FROM FnDespatchDetails()
        WHERE trx_date BETWEEN ? AND ?
        GROUP BY Itemcode, customer_id, collector_id, mc_code
    """
    return db.crm_query(sql, tuple(params))


# Open SOC demand per item -- read-only replication of SP_SOCDetailReport.
SOC_DETAIL_SQL = """
SELECT im.item_code AS ItemCode, MAX(im.item_description) AS ItemName,
       SUM(sod.quantity) AS SocQty, COUNT(DISTINCT soh.header_id) AS SocCount,
       MAX(soh.creation_date) AS LastSoc,
       (SELECT MAX(ic.segment2) FROM ItemCategories ic
        WHERE ic.item_id = im.item_id AND ic.segment1 = 'Performance Chemicals') AS Segment2,
       (SELECT MAX(ic.segment3) FROM ItemCategories ic
        WHERE ic.item_id = im.item_id AND ic.segment1 = 'Performance Chemicals') AS Segment3
FROM SaleOrderHdrs soh
JOIN SaleOrderdtls sod ON sod.header_id = soh.header_id
JOIN ItemMasters im ON im.item_id = sod.item_id
WHERE soh.creation_date >= ?
  AND sod.status = 'OPEN'
  AND EXISTS (SELECT 1 FROM ItemCategories ic
              WHERE ic.item_id = im.item_id
                AND ic.segment1 = 'Performance Chemicals')
GROUP BY im.item_code, im.item_id
HAVING SUM(sod.quantity) > 0
"""


def soc_detail(from_date) -> list[dict]:
    """Open SOC quantity per item for orders created on/after ``from_date``
    (the planning freeze date) — the Adhoc candidates. ``from_date`` is a date
    or ISO string."""
    return db.crm_query(SOC_DETAIL_SQL, (str(from_date),))


# ── user data-scope ("the ultimate table" for the permission dashboard) ───────
# One UNION over the six CRM mapping sources; each branch yields user + persona +
# the raw grant. Collector lists stay CSV here ('1042,34085', '0'/NULL = all) —
# the staging layer explodes them into one row per collector.
#
# Persona -> source (validated against live data, see migrate_user_scope.sql):
#   Sales Executive     UserMarketCircleMappings -> MarketCircles (mc_code)
#   Branch Manager      CollectorMailMappings.bm_user_id (1-4 collectors)
#   Regional Manager    CollectorMailMappings.rm_user_id (multiple collectors)
#   Technical Executive UserCustomerMappings -> CustomerMasters (customer)
#   Technical Head/Mgr  TechnicalUserSegmentMappings (segment4 + collector CSV)
#   Business Head       SpAlertSegmentWorkflowDtls.receiver_id, only receivers
#                       holding the 'Business Head' role (segment3 + collector CSV)
#   Division Head       SpAlertSegmentWorkflowHdrs.divition_head_id (segment2)
USER_SCOPE_SQL = """
SELECT u.line_id AS UserId, u.name AS UserName, u.username AS Username, u.email AS Email,
       'Sales Executive' AS Persona, 'market_circle' AS ScopeType,
       mc.mc_code AS McCode, mc.region AS Region,
       CAST(mc.collector_id AS nvarchar(400)) AS CollectorIds,
       CAST(NULL AS bigint) AS CustomerId, CAST(NULL AS nvarchar(255)) AS CustomerName,
       CAST(NULL AS nvarchar(64)) AS Segment2, CAST(NULL AS nvarchar(64)) AS Segment3,
       CAST(NULL AS nvarchar(64)) AS Segment4,
       'UserMarketCircleMappings' AS Src
FROM UserMarketCircleMappings m
JOIN Users u ON u.line_id = m.user_id AND u.is_active = 1
JOIN MarketCircles mc ON mc.header_id = m.market_circle_id AND mc.is_active = 1
WHERE m.valid_to IS NULL OR m.valid_to >= GETDATE()

UNION ALL
SELECT u.line_id, u.name, u.username, u.email,
       'Branch Manager', 'collector', NULL, NULL,
       CAST(cm.collector_id AS nvarchar(400)),
       NULL, NULL, NULL, NULL, NULL, 'CollectorMailMappings.bm'
FROM CollectorMailMappings cm
JOIN Users u ON u.line_id = cm.bm_user_id AND u.is_active = 1

UNION ALL
SELECT u.line_id, u.name, u.username, u.email,
       'Regional Manager', 'collector', NULL, NULL,
       CAST(cm.collector_id AS nvarchar(400)),
       NULL, NULL, NULL, NULL, NULL, 'CollectorMailMappings.rm'
FROM CollectorMailMappings cm
JOIN Users u ON u.line_id = cm.rm_user_id AND u.is_active = 1

UNION ALL
SELECT u.line_id, u.name, u.username, u.email,
       'Technical Executive', 'customer', NULL, NULL, NULL,
       c.customer_id, c.customer_name, NULL, NULL, NULL, 'UserCustomerMappings'
FROM UserCustomerMappings ucm
JOIN Users u ON u.line_id = ucm.user_id AND u.is_active = 1
JOIN CustomerMasters c ON c.header_id = ucm.customer_hdr_id
WHERE (ucm.valid_to IS NULL OR ucm.valid_to >= GETDATE())
  AND (ucm.valid_from IS NULL OR ucm.valid_from <= GETDATE())

UNION ALL
SELECT u.line_id, u.name, u.username, u.email,
       r.name, 'segment', NULL, NULL,
       t.collector_id,
       NULL, NULL, NULLIF(t.segment2, ''), NULLIF(t.segment3, ''), NULLIF(t.segment4, ''),
       'TechnicalUserSegmentMappings'
FROM TechnicalUserSegmentMappings t
JOIN Users u ON u.line_id = t.user_id AND u.is_active = 1
JOIN Roles r ON r.line_id = t.role_id AND r.name IN ('Technical Head', 'Technical Manager')
WHERE t.valid_to IS NULL OR t.valid_to >= GETDATE()

UNION ALL
SELECT u.line_id, u.name, u.username, u.email,
       'Business Head', 'segment', NULL, NULL,
       d.collector_id,
       NULL, NULL, NULLIF(h.segment2, ''), NULLIF(d.segment3, ''), NULLIF(d.segment4, ''),
       'SpAlertSegmentWorkflowDtls'
FROM SpAlertSegmentWorkflowDtls d
JOIN SpAlertSegmentWorkflowHdrs h ON h.header_id = d.header_id
JOIN Users u ON u.line_id = d.receiver_id AND u.is_active = 1
WHERE d.receiver_id IS NOT NULL AND d.receiver_id <> 0
  AND EXISTS (SELECT 1 FROM UserRoles ur JOIN Roles r2 ON r2.line_id = ur.role_id
              WHERE ur.user_id = u.line_id AND r2.name = 'Business Head')

UNION ALL
SELECT u.line_id, u.name, u.username, u.email,
       'Division Head', 'segment', NULL, NULL, NULL,
       NULL, NULL, NULLIF(h.segment2, ''), NULL, NULL, 'SpAlertSegmentWorkflowHdrs'
FROM SpAlertSegmentWorkflowHdrs h
JOIN Users u ON u.line_id = h.divition_head_id AND u.is_active = 1
"""


def user_scope() -> dict:
    """The raw user->data-grant rows plus the collector id->name map the staging
    layer needs to explode CSV collector lists. Returns
    ``{"grants": [...], "collectors": {id: name}}``."""
    grants = db.crm_query(USER_SCOPE_SQL)
    collectors = {int(r["collector_id"]): r["name"]
                  for r in db.crm_query("SELECT collector_id, name FROM Collectors")
                  if r.get("collector_id") is not None}
    return {"grants": grants, "collectors": collectors}


# Open committed order lines for the Commitment-Risk page.
#
# CRM maintains dbo.SocPendingDetails: a daily snapshot of every pending SOC
# line. Preferred over joining FnOrderDtlPending x FnScheduleDtlPending live
# because it (a) covers the WHOLE pending book, not just a recent window —
# more than half of all pending lines are older than 120 days — (b) reads in
# ~4s instead of ~63s, and (c) already carries the customer-requested date,
# segments, market circle, sales executive and the warehouse's reschedule note.
# Checked against the live functions over the same window: 6,160 of ~6,200
# lines match exactly; the handful that differ are that day's activity since
# the snapshot was built.
#
# customer_id and the quotation number are not in the snapshot, so both are
# joined in (verified 1:1 — neither join multiplies rows).
ORDER_COMMIT_SQL = """
SELECT s.ORDER_NO AS OrderNo, s.quotation_id AS SocLineId,
       h.quotation_number AS OrderRef, s.ORDER_DATE AS SocDate,
       cm.customer_id AS CustomerId, s.CUSTOMER_NAME AS CustomerName,
       s.COLLECTOR AS Collector, s.market_circle AS McCode,
       s.ITEMCODE AS ItemCode, s.item_name AS ItemName, s.Item_group AS ItemGroup,
       s.INVENTORY_ORG_NAME AS InvOrg, s.category AS SalesType,
       s.SCHEDULED_QTY AS Qty, s.DESPATCHED_QTY AS Despatched, s.BALANCE_QTY AS Balance,
       s.SCHEDULE_DATE AS SchedDate, CAST(s.RESCHEDULE_DATE AS date) AS ReschedDate,
       s.customer_req_date AS CustReqDate,
       s.RESCHEDULE_REASON AS ReschedReason,
       s.WH_INCHARGE_RESCHEDULE_COMMENTS AS WhComments,
       s.EXECUTIVE_NAME AS Executive, s.Dispatch_PER AS DispatchPct,
       s.SEGMENT2 AS Segment2, s.SEGMENT3 AS Segment3, s.SEGMENT4 AS Segment4,
       s.SyncDate AS SnapshotAt
FROM SocPendingDetails s
LEFT JOIN SaleOrderHdrs h ON h.header_id = s.ORDER_NO
LEFT JOIN CustomerMasters cm ON cm.customer_number = s.CUSTOMER_NUMBER
WHERE s.BALANCE_QTY > 0
"""


def order_commit() -> list[dict]:
    """Every open committed order line, from CRM's pending-SOC snapshot."""
    return db.crm_query(ORDER_COMMIT_SQL)


# ── demand ledger: the projection at the grain CRM actually stores it ────────
#
# business_plan_projection_rows() aggregates the plan to item x collector, which
# loses the customer. The Demand-Protection page needs the customer, because the
# whole question is "is MY projection for THIS customer covered by a firm order".
# Same table, same approved-only rule, one level deeper.
#
# Two joins are added so the ledger can meet the rest of the warehouse:
#   mc_code   from the customer's PRIMARY CustomerSites row — the projection
#             carries no market circle, and the Sales-Executive persona is
#             scoped by one. Resolves for 100% of projected customers and
#             agrees with the dispatch cube on 99.0% of customer/MC pairs.
#   item_code from itemmasters — the plan stores an item NAME; open orders and
#             the dispatch cube store a CODE. 99.4% of projected items resolve
#             (100.0% by quantity); the rest stay NULL and join by name.
PROJECTION_CUSTOMER_SQL = """
SELECT d.customer_id AS CustomerId, MAX(d.customer_name) AS CustomerName,
       d.collector_id AS CollectorId, MAX(d.collector) AS Collector,
       MAX(cs.mc_code) AS McCode,
       MAX(im.item_code) AS ItemCode, d.item_description AS ItemName,
       MAX(d.segment2) AS Segment2, MAX(d.segment3) AS Segment3,
       MAX(d.segment4) AS Segment4,
       SUM(ISNULL(d.jc{n}_week1_user_dfn_qty, 0)) AS Week1Q,
       SUM(ISNULL(d.jc{n}_week2_user_dfn_qty, 0)) AS Week2Q,
       SUM(ISNULL(j.jc_nextmonth1_qty, 0)) AS Next1Q,
       SUM(ISNULL(j.jc_nextmonth2_qty, 0)) AS Next2Q
FROM SCBusinessMonthlyPlanDtls d
JOIN SCBusinessMonthlyPlanHdrs h ON h.header_id = d.header_id
LEFT JOIN SCBusinessMonthlyPlanJCDtls j
       ON j.header_id = d.line_id
      AND j.jc_type = (SELECT TOP 1 line_id FROM JourneyCalendars
                       WHERE acc_year = h.acc_year AND name = 'JC{n}' AND is_active = 1)
OUTER APPLY (SELECT TOP 1 s.mc_code FROM CustomerSites s
             WHERE s.customer_id = d.customer_id AND s.mc_code IS NOT NULL
             ORDER BY CASE WHEN s.primary_flag = 'Y' THEN 0 ELSE 1 END, s.line_id) cs
OUTER APPLY (SELECT TOP 1 m.item_code FROM itemmasters m
             WHERE m.item_description = d.item_description
               AND m.item_code IS NOT NULL) im
WHERE h.acc_year = ?
  {status_filter}
GROUP BY d.customer_id, d.collector_id, d.item_description
HAVING SUM(ISNULL(d.jc{n}_week1_user_dfn_qty, 0)
         + ISNULL(d.jc{n}_week2_user_dfn_qty, 0)) > 0
"""


def projection_customer(acc_year: str, jc_number: int,
                        approved_only: bool = True) -> list[dict]:
    """Approved projection for one JC per (customer, item, collector) — the
    demand-ledger grain. Rows with a zero JC total are dropped at source."""
    n = int(jc_number)
    if not 1 <= n <= 13:
        raise ValueError(f"jc_number out of range (1-13): {jc_number}")
    return db.crm_query(
        PROJECTION_CUSTOMER_SQL.format(n=n, status_filter=_status_filter(n, approved_only)),
        (acc_year,))


SOURCES = {
    "pto_pts": pto_pts, "soc_pending": soc_pending,
    "quote_details": quote_details, "dispatch_details": dispatch_details,
    "po_open_intransit": po_open_intransit,
    "po_open_intransit_detail": po_open_intransit_detail,
}
