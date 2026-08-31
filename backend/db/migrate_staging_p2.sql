-- ---------------------------------------------------------------------------
-- Phase 2 of the sync-to-DB architecture (see ARCHITECTURE.md) — the remaining
-- current-state CRM sources (no date/JC parameters), staged like Phase 1.
--
--   stg_stock_details  - full on-hand stock (BiStockDetail, item x org x subinv)
--   stg_item_business  - item_code -> Business (segment2)   [raw rows, deduped on read]
--   stg_pto_pts        - item master + PTO/PTS flag           [raw rows, deduped on read]
--
-- Replaced wholesale each sync (transactional). Run as root, or as planning_app
-- if it has been granted CREATE (see ARCHITECTURE.md).
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_stock_details (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  organization VARCHAR(120) NULL,
  item_code    VARCHAR(64)  NOT NULL,
  item_desc    VARCHAR(255) NULL,
  subinv       VARCHAR(64)  NULL,
  qty          DECIMAL(18,3) NOT NULL DEFAULT 0,
  item_cost    DECIMAL(18,4) NULL,
  KEY idx_item (item_code),
  KEY idx_org (organization)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- item -> business. An item may carry several categories, so item_code is NOT
-- unique here; the reader (business_map) applies the "Raw Material wins" rule.
CREATE TABLE IF NOT EXISTS stg_item_business (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code VARCHAR(64)  NOT NULL,
  business  VARCHAR(120) NULL,
  KEY idx_item (item_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- item master + PTO/PTS flag. Raw rows (a name may map to several codes); the
-- readers (_pto_map / _template_items) de-dupe with their own precedence rules.
CREATE TABLE IF NOT EXISTS stg_pto_pts (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_id   VARCHAR(64)  NULL,
  item_code VARCHAR(64)  NULL,
  item_name VARCHAR(255) NULL,
  uom       VARCHAR(32)  NULL,
  segment1  VARCHAR(64)  NULL,
  segment2  VARCHAR(64)  NULL,
  segment3  VARCHAR(64)  NULL,
  segment4  VARCHAR(64)  NULL,
  itemtype  VARCHAR(16)  NULL,
  KEY idx_name (item_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
