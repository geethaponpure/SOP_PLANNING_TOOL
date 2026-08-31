-- ---------------------------------------------------------------------------
-- Phase 1 of the "sync-to-DB, serve-from-DB" architecture (see ARCHITECTURE.md).
--
-- Adds:
--   sync_runs        - one row per sync attempt (freshness log + audit)   [APPEND]
--   sync_requests    - "Refresh now" queue polled by the worker           [APPEND]
--   stg_stock_lots   - current lot-wise on-hand stock snapshot            [REPLACE]
--   stg_item_segments- current item -> division/segment map               [REPLACE]
--
-- Staging tables hold the CURRENT state only (one snapshot). The worker
-- replaces their contents inside a single transaction each sync, so API
-- readers always see a complete previous snapshot (never empty/partial).
--
-- Run once:  mysql -u root -p planning_tool < migrate_staging.sql
-- (or let backend/db/run_setup.ps1 apply it)
-- ---------------------------------------------------------------------------

USE planning_tool;

-- Freshness log: append one row per sync run.
CREATE TABLE IF NOT EXISTS sync_runs (
  run_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  source      VARCHAR(32)  NOT NULL,          -- 'stock_lots' | 'item_segments' | 'all'
  started_at  DATETIME     NOT NULL,
  finished_at DATETIME     NULL,
  status      VARCHAR(16)  NOT NULL DEFAULT 'running',   -- running | ok | error
  row_count   INT          NULL,
  error       VARCHAR(255) NULL,
  KEY idx_source_started (source, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- "Refresh now" queue: the API appends a request, the worker claims + runs it.
CREATE TABLE IF NOT EXISTS sync_requests (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  source       VARCHAR(32)  NOT NULL DEFAULT 'all',
  requested_at DATETIME     NOT NULL,
  status       VARCHAR(16)  NOT NULL DEFAULT 'pending',  -- pending | done
  claimed_at   DATETIME     NULL,
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Current lot-wise on-hand stock (mirrors CRM stock_lots). Replaced each sync.
CREATE TABLE IF NOT EXISTS stg_stock_lots (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code    VARCHAR(64)  NOT NULL,
  item_desc    VARCHAR(255) NULL,
  organization VARCHAR(120) NULL,
  org_code     VARCHAR(32)  NULL,
  subinv       VARCHAR(64)  NULL,
  lot          VARCHAR(80)  NULL,
  qty          DECIMAL(18,3) NOT NULL DEFAULT 0,
  aging_date   DATE         NULL,
  age_days     INT          NULL,
  KEY idx_item (item_code),
  KEY idx_org (organization)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Current item -> division / segment map (mirrors CRM item_segments). One row
-- per item_code. Replaced each sync.
CREATE TABLE IF NOT EXISTS stg_item_segments (
  item_code       VARCHAR(64)  NOT NULL PRIMARY KEY,
  item_name       VARCHAR(255) NULL,
  division_target VARCHAR(64)  NULL,
  segment1        VARCHAR(64)  NULL,
  segment2        VARCHAR(64)  NULL,
  segment3        VARCHAR(64)  NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
