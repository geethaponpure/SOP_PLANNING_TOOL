-- ---------------------------------------------------------------------------
-- Phase 2b — dispatch (the last request-time CRM source in the RM-Plan path).
--
-- dispatch_by_jc() returns a WIDE row per (item x collector) with one qty column
-- per JC (jc0..jc{n-1}); the column set changes with the JC list passed. We store
-- it LONG (one row per item x collector x jc_index) and pivot back to jcN on read,
-- so the exact wide shape is reproduced without dynamic columns.
--
-- Two variants: 'jc3' (last 3 JCs, dispatch average) and 'jc13' (last 13, MSL).
-- Only non-zero qty is stored (sparse); read fills missing slots with 0.
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_dispatch (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  variant      VARCHAR(8)   NOT NULL,          -- 'jc3' | 'jc13'
  item_code    VARCHAR(64)  NULL,
  item_name    VARCHAR(255) NULL,
  collector    VARCHAR(120) NULL,
  collector_id VARCHAR(64)  NULL,
  jc_index     TINYINT      NOT NULL,
  qty          DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_variant (variant)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
