-- MSL (Minimum Stock Level) snapshots — one snapshot per JC (sliding 13-JC window).
-- Run once by a DBA (the app's MySQL login is DML-only and cannot CREATE):
--   mysql -u root -p planning_tool < backend/db/migrate_msl.sql
-- Until this runs, the app uses a JSON fallback (msl_store.json) automatically.

CREATE TABLE IF NOT EXISTS sc_msl_snapshot (
  reference   VARCHAR(60)  NOT NULL PRIMARY KEY,   -- e.g. msl_jc5_2026
  jc_label    VARCHAR(20)  NOT NULL DEFAULT '',    -- current JC (JC5)
  fy          VARCHAR(20)  NOT NULL DEFAULT '',    -- 2026-2027
  n_jcs       INT          NOT NULL DEFAULT 13,
  jc_from     VARCHAR(20)  NOT NULL DEFAULT '',    -- window start date
  jc_to       VARCHAR(20)  NOT NULL DEFAULT '',    -- window end date
  n_items     INT          NOT NULL DEFAULT 0,
  created_by  VARCHAR(120) NOT NULL DEFAULT '',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sc_msl_item (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  reference         VARCHAR(60)  NOT NULL,
  item_code         VARCHAR(40)  NOT NULL,
  item_name         VARCHAR(200) NOT NULL DEFAULT '',
  activity          VARCHAR(24)  NOT NULL DEFAULT '',   -- Manufacturing / Repack-Relabel / Trading
  business          VARCHAR(60)  NOT NULL DEFAULT '',
  avg_qty_per_jc    DECIMAL(18,1) NOT NULL DEFAULT 0,   -- average one-JC sales
  freq_jcs          INT          NOT NULL DEFAULT 0,    -- JCs (of 13) with movement
  customer_coverage INT          NOT NULL DEFAULT 0,    -- unique customers over 13 JCs
  total_qty         DECIMAL(18,1) NOT NULL DEFAULT 0,
  msl               DECIMAL(18,1) NOT NULL DEFAULT 0,   -- 50% of avg one-JC sales
  warehouse_stock   DECIMAL(18,1) NOT NULL DEFAULT 0,   -- current on-hand at warehouse orgs
  branch_stock      DECIMAL(18,1) NOT NULL DEFAULT 0,   -- current on-hand at branches
  onhand_stock      DECIMAL(18,1) NOT NULL DEFAULT 0,   -- warehouse + branch
  KEY idx_ref (reference),
  KEY idx_ref_activity (reference, activity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
