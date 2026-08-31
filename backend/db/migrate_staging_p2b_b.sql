-- ---------------------------------------------------------------------------
-- Phase 2b, Bucket B (see ARCHITECTURE.md) — the CONTEXT-KEYED CRM sources whose
-- content depends on today's planning context (planning JC / acc year / date
-- windows / freeze). The worker computes that context (jc_calendar), writes it to
-- sync_context, and stages each source FOR that context.
--
--   sync_context     - the planning context the worker last synced for (provenance
--                      + JC-boundary check)
--   stg_projection   - business-plan projection, keyed by (acc_year, jc)
--   stg_soc_pending  - despatch-pending SOC, scope 'all' | 'mfg' (current window)
--   stg_soc_detail   - post-freeze open SOC (current freeze)
--   stg_intransit    - open-PO in-transit detail (current recency window)
--
-- Fixed-shape sources; the dynamic per-JC ones (dispatch, projection_rows) are a
-- separate step. Full transactional replace per sync.
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS sync_context (
  id             TINYINT      NOT NULL PRIMARY KEY,   -- always 1 (single row)
  plan_jc        INT          NULL,
  acc_year       VARCHAR(9)   NULL,
  soc_from       DATE         NULL,
  soc_to         DATE         NULL,
  freeze_date    VARCHAR(20)  NULL,
  intransit_from DATE         NULL,
  blanket_po_qty DECIMAL(18,2) NULL,
  computed_at    DATETIME     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_projection (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  acc_year   VARCHAR(9)   NOT NULL,
  jc         INT          NOT NULL,
  item_name  VARCHAR(255) NULL,
  segment2   VARCHAR(64)  NULL,
  segment3   VARCHAR(64)  NULL,
  current_q  DECIMAL(18,3) NOT NULL DEFAULT 0,
  next1_q    DECIMAL(18,3) NOT NULL DEFAULT 0,
  next2_q    DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_ctx (acc_year, jc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_soc_pending (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  scope       VARCHAR(8)   NOT NULL,          -- 'all' | 'mfg'
  item_code   VARCHAR(64)  NULL,
  item_desc   VARCHAR(255) NULL,
  pending_qty DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_scope (scope)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_soc_detail (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code VARCHAR(64)  NULL,
  item_name VARCHAR(255) NULL,
  soc_qty   DECIMAL(18,3) NOT NULL DEFAULT 0,
  soc_count INT          NULL,
  last_soc  VARCHAR(30)  NULL,
  segment2  VARCHAR(64)  NULL,
  segment3  VARCHAR(64)  NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_intransit (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code        VARCHAR(64)  NULL,
  item_desc        VARCHAR(255) NULL,
  po_number        VARCHAR(48)  NULL,
  po_date          VARCHAR(20)  NULL,
  vendor_name      VARCHAR(255) NULL,
  org_name         VARCHAR(120) NULL,
  procurement_type VARCHAR(64)  NULL,
  quantity         DECIMAL(18,3) NOT NULL DEFAULT 0,
  received         DECIMAL(18,3) NOT NULL DEFAULT 0,
  cancelled        DECIMAL(18,3) NOT NULL DEFAULT 0,
  in_transit       DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_item (item_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
