-- ---------------------------------------------------------------------------
-- Commercial pipeline for My Dashboard: the annual plan and the open quote book.
--
--   stg_annual_plan  - annual potential / budget per customer x item x collector
--                      (SCBusinessMonthlyPlanHdrs, one row per plan line)
--   stg_open_quote   - one row per OPEN quotation line (QuotationHdrs/Dtls)
--
-- Both carry the full scope key set (mc_code, collector_id, customer_id,
-- segment2-4) so staging._scope_where filters them exactly like the dispatch
-- cube, and item_code/item_name so the Performance Chemicals gate applies.
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_annual_plan (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  acc_year        VARCHAR(9)    NOT NULL,
  customer_id     BIGINT        NULL,
  customer_name   VARCHAR(255)  NULL,
  collector_id    BIGINT        NULL,
  collector       VARCHAR(400)  NULL,
  mc_code         VARCHAR(32)   NULL,
  item_code       VARCHAR(64)   NULL,
  item_name       VARCHAR(255)  NULL,
  segment2        VARCHAR(64)   NULL,
  segment3        VARCHAR(64)   NULL,
  segment4        VARCHAR(64)   NULL,
  -- quantities in KG; values in RUPEES (CRM stores lakhs, converted at sync)
  potential_qty   DECIMAL(18,3) NOT NULL DEFAULT 0,
  potential_value DECIMAL(18,2) NOT NULL DEFAULT 0,
  budget_qty      DECIMAL(18,3) NOT NULL DEFAULT 0,
  budget_value    DECIMAL(18,2) NOT NULL DEFAULT 0,
  KEY idx_year (acc_year),
  KEY idx_cust (customer_id),
  KEY idx_coll (collector_id),
  KEY idx_item (item_name(80))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_open_quote (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  quote_id      BIGINT        NULL,
  quote_date    DATE          NULL,
  status        VARCHAR(48)   NULL,
  customer_id   BIGINT        NULL,
  customer_name VARCHAR(255)  NULL,
  collector_id  BIGINT        NULL,
  collector     VARCHAR(400)  NULL,
  mc_code       VARCHAR(32)   NULL,
  item_code     VARCHAR(64)   NULL,
  item_name     VARCHAR(255)  NULL,
  segment2      VARCHAR(64)   NULL,
  segment3      VARCHAR(64)   NULL,
  segment4      VARCHAR(64)   NULL,
  qty           DECIMAL(18,3) NOT NULL DEFAULT 0,
  value_        DECIMAL(18,2) NOT NULL DEFAULT 0,
  KEY idx_item (item_name(80)),
  KEY idx_date (quote_date),
  KEY idx_cust (customer_id),
  KEY idx_coll (collector_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
