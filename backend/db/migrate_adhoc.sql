-- Migration: JC planning + Adhoc evaluation tables.
-- Run as ROOT (needs CREATE privilege; planning_app only has DML):
--     mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_adhoc.sql"
-- or open in MySQL Workbench and Execute. Re-running setup.sql works too.
USE planning_tool;

-- JC calendar with the planning freeze date (2nd day of the 3rd week of the JC).
CREATE TABLE IF NOT EXISTS JC_MASTER (
  fy          VARCHAR(9)  NOT NULL,
  jc_number   INT         NOT NULL,
  start_date  DATE        NOT NULL,
  end_date    DATE        NOT NULL,
  freeze_date DATE        NOT NULL,
  PRIMARY KEY (fy, jc_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per saved JC planning run (unique Plan ID).
CREATE TABLE IF NOT EXISTS JC_PLAN (
  plan_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_datetime  DATETIME      NOT NULL,
  fy             VARCHAR(9)     NOT NULL,
  jc_number      INT            NOT NULL,
  plan_type      VARCHAR(16)    NOT NULL DEFAULT 'JC',    -- 'JC' or 'ADHOC'
  planned_fg_qty DECIMAL(18,2)  NOT NULL DEFAULT 0,
  planned_rm_qty DECIMAL(18,2)  NOT NULL DEFAULT 0,
  fg_count       INT            NOT NULL DEFAULT 0,
  rm_count       INT            NOT NULL DEFAULT 0,
  note           VARCHAR(255),
  KEY idx_jc (fy, jc_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- RM allocation details for a plan (the RM already reserved by the JC plan).
CREATE TABLE IF NOT EXISTS RM_ALLOCATION_LEDGER (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_id       BIGINT         NOT NULL,
  rm_code       VARCHAR(64)    NOT NULL,
  rm_desc       VARCHAR(255),
  allocated_qty DECIMAL(18,2)  NOT NULL DEFAULT 0,
  plan_type     VARCHAR(16)    NOT NULL DEFAULT 'JC',
  KEY idx_plan (plan_id),
  KEY idx_rm (rm_desc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Per-item adhoc order evaluation log (projection vs pending-SOC vs order).
CREATE TABLE IF NOT EXISTS ADHOC_EVALUATION (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  eval_datetime   DATETIME      NOT NULL,
  plan_id         BIGINT,                        -- JC plan whose RM was deducted
  fy              VARCHAR(9),
  jc_number       INT,
  item_name       VARCHAR(255)  NOT NULL,
  projected_qty   DECIMAL(18,2) NOT NULL DEFAULT 0,
  pending_soc_qty DECIMAL(18,2) NOT NULL DEFAULT 0,
  order_qty       DECIMAL(18,2) NOT NULL DEFAULT 0,
  adhoc_qty       DECIMAL(18,2) NOT NULL DEFAULT 0,
  status          VARCHAR(16)   NOT NULL,         -- 'covered' | 'exceeds' | 'new'
  KEY idx_eval (eval_datetime)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
