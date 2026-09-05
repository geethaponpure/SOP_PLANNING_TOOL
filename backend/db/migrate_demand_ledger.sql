-- ---------------------------------------------------------------------------
-- Demand ledger (Phase 1 of the Projection -> SOC -> Supply -> Commitment work).
--
-- stg_projection_rows already stages the projection, but only at
-- item x collector — the CUSTOMER is dropped. Every question the proposal asks
-- ("is MY projection for THIS customer protected?", "who else committed against
-- this item?") needs the customer, so this table stages the same plan at the
-- grain CRM actually holds it: customer x item x collector x JC.
--
-- Source: SCBusinessMonthlyPlanDtls (approved rows only, jc{n}_status = 5).
--   current_q = jc{n}_week1 + jc{n}_week2 (the JC total the planner entered)
--   week1_q / week2_q kept separately — that fortnight split is the ONLY
--   requirement-date signal a projection carries (there is no day-level date).
--   next1_q / next2_q = the following two JCs (SCBusinessMonthlyPlanJCDtls).
--
-- mc_code is resolved from the customer's PRIMARY CustomerSites row: 100% of
-- projected customers resolve, and it agrees with the dispatch cube's own
-- mc_code on 9,940 of 10,044 customer/market-circle pairs (99.0%).
-- item_code is joined from itemmasters so the ledger can join to open orders
-- (stg_order_commit) and dispatch (stg_dispatch_scope) on a code, not a name.
--
-- Replaced per (acc_year, jc) each sync.  Run once:
--   mysql -u root -p planning_tool < migrate_demand_ledger.sql
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_projection_customer (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  acc_year      VARCHAR(9)    NOT NULL,
  jc            INT           NOT NULL,
  customer_id   BIGINT        NULL,
  customer_name VARCHAR(255)  NULL,
  collector_id  BIGINT        NULL,
  collector     VARCHAR(120)  NULL,
  mc_code       VARCHAR(32)   NULL,
  item_code     VARCHAR(64)   NULL,             -- NULL when the name has no master
  item_name     VARCHAR(255)  NOT NULL,
  segment2      VARCHAR(64)   NULL,
  segment3      VARCHAR(64)   NULL,
  segment4      VARCHAR(64)   NULL,
  week1_q       DECIMAL(18,3) NOT NULL DEFAULT 0,
  week2_q       DECIMAL(18,3) NOT NULL DEFAULT 0,
  current_q     DECIMAL(18,3) NOT NULL DEFAULT 0,
  next1_q       DECIMAL(18,3) NOT NULL DEFAULT 0,
  next2_q       DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_ctx (acc_year, jc),
  KEY idx_cust (acc_year, jc, customer_id),
  KEY idx_item (acc_year, jc, item_code),
  KEY idx_coll (collector_id),
  KEY idx_mc (mc_code),
  KEY idx_seg3 (segment3),
  KEY idx_seg4 (segment4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
