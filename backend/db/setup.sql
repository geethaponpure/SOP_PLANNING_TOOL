-- ---------------------------------------------------------------------------
-- Planning Tool — app-owned MySQL store (dedicated database + user).
-- Run ONCE with a privileged (root) account:
--
--     mysql -u root -p < setup.sql
--
-- It creates the `planning_tool` database, a dedicated low-privilege app user,
-- and the Vooki FG name -> SKU mapping table. The app connects as `planning_app`
-- (see backend/.env MYSQL_* keys). Change the password below AND in backend/.env
-- if you want something other than the default.
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS planning_tool
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'planning_app'@'localhost' IDENTIFIED BY 'Planning@2026';
CREATE USER IF NOT EXISTS 'planning_app'@'127.0.0.1' IDENTIFIED BY 'Planning@2026';

GRANT SELECT, INSERT, UPDATE, DELETE ON planning_tool.* TO 'planning_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON planning_tool.* TO 'planning_app'@'127.0.0.1';
FLUSH PRIVILEGES;

USE planning_tool;

-- Vooki finished-good name -> stock SKU-code mapping.
-- Each Vooki FG SKU (as it appears in CRM stock) maps to exactly one Vooki
-- product (the BOM-assembly name shown on the Vooki Planning page), so FG stock
-- is attributed to the correct product when unpacked into units / KG-Lit.
CREATE TABLE IF NOT EXISTS vooki_fg_map (
  sku_code     VARCHAR(64)  NOT NULL PRIMARY KEY,
  product_name VARCHAR(255) NOT NULL,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                 ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_product (product_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Admin-added Vooki FG SKUs (beyond the master CSV), loaded from CRM Vooki
-- Division item descriptions. These extend the Vooki FG names / planning scope.
CREATE TABLE IF NOT EXISTS vooki_fg_sku (
  sku_code   VARCHAR(64)  NOT NULL PRIMARY KEY,
  item_desc  VARCHAR(255) NOT NULL,
  source     VARCHAR(32)  NOT NULL DEFAULT 'manual',
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
               ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- JC planning + Adhoc evaluation (see migrate_adhoc.sql for details).
CREATE TABLE IF NOT EXISTS JC_MASTER (
  fy VARCHAR(9) NOT NULL, jc_number INT NOT NULL,
  start_date DATE NOT NULL, end_date DATE NOT NULL, freeze_date DATE NOT NULL,
  PRIMARY KEY (fy, jc_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS JC_PLAN (
  plan_id BIGINT AUTO_INCREMENT PRIMARY KEY, plan_datetime DATETIME NOT NULL,
  fy VARCHAR(9) NOT NULL, jc_number INT NOT NULL, plan_type VARCHAR(16) NOT NULL DEFAULT 'JC',
  planned_fg_qty DECIMAL(18,2) NOT NULL DEFAULT 0, planned_rm_qty DECIMAL(18,2) NOT NULL DEFAULT 0,
  fg_count INT NOT NULL DEFAULT 0, rm_count INT NOT NULL DEFAULT 0, note VARCHAR(255),
  KEY idx_jc (fy, jc_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS RM_ALLOCATION_LEDGER (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, plan_id BIGINT NOT NULL,
  rm_code VARCHAR(64) NOT NULL, rm_desc VARCHAR(255), allocated_qty DECIMAL(18,2) NOT NULL DEFAULT 0,
  plan_type VARCHAR(16) NOT NULL DEFAULT 'JC', activity VARCHAR(20) NULL,
  KEY idx_plan (plan_id), KEY idx_rm (rm_desc), KEY idx_activity (activity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ADHOC_EVALUATION (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, eval_datetime DATETIME NOT NULL, plan_id BIGINT,
  fy VARCHAR(9), jc_number INT, item_name VARCHAR(255) NOT NULL,
  projected_qty DECIMAL(18,2) NOT NULL DEFAULT 0, pending_soc_qty DECIMAL(18,2) NOT NULL DEFAULT 0,
  order_qty DECIMAL(18,2) NOT NULL DEFAULT 0, adhoc_qty DECIMAL(18,2) NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL, KEY idx_eval (eval_datetime)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS PLAN_FG_DEMAND (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, plan_id BIGINT NOT NULL, item_name VARCHAR(255) NOT NULL,
  current_jc DECIMAL(18,2) NOT NULL DEFAULT 0, next_jc1 DECIMAL(18,2) NOT NULL DEFAULT 0,
  next_jc2 DECIMAL(18,2) NOT NULL DEFAULT 0, source VARCHAR(16) NOT NULL DEFAULT 'CRM',
  bom_class VARCHAR(20) NULL, bom_variant VARCHAR(80) NULL,
  KEY idx_plan (plan_id), KEY idx_bomclass (bom_class)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
