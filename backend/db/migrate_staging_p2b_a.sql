-- ---------------------------------------------------------------------------
-- Phase 2b, Bucket A (see ARCHITECTURE.md) — the near-parameter-free CRM sources
-- (their only "parameter" is a static admin setting: aged_rm_days / vooki_business).
-- Staged like Phase 1/2 (full transactional replace each sync).
--
--   stg_stock_aged    - aged on-hand stock (BiStockDetail, age > aged_rm_days)
--   stg_vooki_items   - Vooki-division item codes (segment2 = vooki_business)
--   stg_soc_schedule  - scheduled SOC pending (FnOrderDtlPending / FnScheduleDtlPending)
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_stock_aged (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  organization VARCHAR(120) NULL,
  item_code    VARCHAR(64)  NOT NULL,
  item_desc    VARCHAR(255) NULL,
  subinv       VARCHAR(64)  NULL,
  qty          DECIMAL(18,3) NOT NULL DEFAULT 0,
  item_cost    DECIMAL(18,4) NULL,
  max_age_days INT          NULL,
  KEY idx_item (item_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_vooki_items (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code VARCHAR(64)  NOT NULL,
  item_desc VARCHAR(255) NULL,
  KEY idx_item (item_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_soc_schedule (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code     VARCHAR(64)  NULL,
  item_desc     VARCHAR(255) NULL,
  schedule_date DATE         NULL,
  qty           DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_item (item_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
