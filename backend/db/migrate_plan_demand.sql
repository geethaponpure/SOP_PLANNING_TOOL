-- Migration: per-FG demand captured for each saved plan (audit of what was
-- planned, and from which source — CRM projection / Excel upload / consolidated).
-- Run as ROOT (needs CREATE):
--     mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_plan_demand.sql"
-- or open in MySQL Workbench and Execute. Re-running setup.sql works too.
USE planning_tool;

CREATE TABLE IF NOT EXISTS PLAN_FG_DEMAND (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_id    BIGINT        NOT NULL,
  item_name  VARCHAR(255)  NOT NULL,
  current_jc DECIMAL(18,2) NOT NULL DEFAULT 0,
  next_jc1   DECIMAL(18,2) NOT NULL DEFAULT 0,
  next_jc2   DECIMAL(18,2) NOT NULL DEFAULT 0,
  source     VARCHAR(16)   NOT NULL DEFAULT 'CRM',   -- CRM | EXCEL | CONSOLIDATED
  KEY idx_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
