-- ---------------------------------------------------------------------------
-- Permission dashboard data (My Dashboard page).
--
--   stg_dispatch_scope - dispatch aggregated per JC x item x customer x
--                        collector x market-circle. Carries EVERY dimension a
--                        persona scope can filter on (mc_code for Sales
--                        Executives, collector for BM/RM, customer for
--                        Technical Executives, item -> segment via
--                        stg_item_segments for the segment personas).
--                        Replaced in full each sync (13-JC window, same window
--                        as stg_dispatch jc13 / MSL).
--
--   stg_item_segments  - gains segment4 (product family) so Technical
--                        Head/Manager grants (segment4-level) can be resolved.
--
-- Run once:  mysql -u root -p planning_tool < migrate_dashboard.sql
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_dispatch_scope (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  jc_index      INT           NOT NULL,            -- 0 = oldest JC in the window
  item_code     VARCHAR(64)   NOT NULL,
  item_name     VARCHAR(255)  NULL,
  customer_id   BIGINT        NULL,
  customer_name VARCHAR(255)  NULL,
  collector_id  BIGINT        NULL,
  collector     VARCHAR(120)  NULL,
  mc_code       VARCHAR(32)   NULL,
  qty           DECIMAL(18,3) NOT NULL DEFAULT 0,  -- despatched KG in that JC
  value_        DECIMAL(18,2) NOT NULL DEFAULT 0,  -- despatch value in that JC
  KEY idx_jc (jc_index),
  KEY idx_customer (customer_id),
  KEY idx_collector (collector_id),
  KEY idx_mc (mc_code),
  KEY idx_item (item_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- segment4 (product family) for Technical Head/Manager scope resolution.
-- (Re-running errors with "Duplicate column name" — harmless, ignore.)
ALTER TABLE stg_item_segments ADD COLUMN segment4 VARCHAR(64) NULL;

-- Denormalized item segments ON the cube (filled at sync time from
-- stg_item_segments): dashboard queries filter/group single-table with
-- indexes — the request-time JOIN over 134k rows took ~20s, this takes ms.
ALTER TABLE stg_dispatch_scope
  ADD COLUMN segment2 VARCHAR(64) NULL,
  ADD COLUMN segment3 VARCHAR(64) NULL,
  ADD COLUMN segment4 VARCHAR(64) NULL,
  ADD KEY idx_seg2 (segment2),
  ADD KEY idx_seg3 (segment3),
  ADD KEY idx_seg4 (segment4);
