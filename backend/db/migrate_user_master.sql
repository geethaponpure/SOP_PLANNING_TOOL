-- Migration: App User Master + per-user module/menu access (admin approval page).
-- Run as ROOT (needs CREATE privilege — planning_app only has DML):
--     mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_user_master.sql"
-- or open this file in MySQL Workbench and Execute.
-- Until this runs, the app persists to backend/user_master.json (JSON fallback);
-- afterwards use the "Import JSON → DB" button on the User Master page to migrate those rows.
USE planning_tool;

CREATE TABLE IF NOT EXISTS sc_app_user (
  user_code     VARCHAR(32)  NOT NULL PRIMARY KEY,   -- CRM dbo.Users.user_code
  crm_line_id   BIGINT       NULL,                    -- CRM dbo.Users.line_id
  name          VARCHAR(200) NOT NULL,
  username      VARCHAR(100) NULL,
  email         VARCHAR(200) NULL,
  mobile        VARCHAR(40)  NULL,
  department    VARCHAR(100) NULL,
  designation   VARCHAR(100) NULL,
  status        VARCHAR(16)  NOT NULL DEFAULT 'active',   -- active | disabled
  -- login password stored as a salted one-way hash (pbkdf2_sha256$iters$salt$hash),
  -- NEVER plaintext. New users default to 'pure@123' (hashed by the app on insert).
  password_hash VARCHAR(255) NULL,
  approved_by   VARCHAR(120) NULL,
  approved_at   DATETIME     NULL,
  KEY idx_dept (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- If sc_app_user already existed from an earlier migration, add the column once:
--   ALTER TABLE sc_app_user ADD COLUMN password_hash VARCHAR(255) NULL AFTER status;

CREATE TABLE IF NOT EXISTS sc_app_user_menu (
  id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_code  VARCHAR(32)  NOT NULL,
  menu_id    VARCHAR(64)  NOT NULL,     -- app nav id, e.g. 'supply', 'srdms'
  menu_label VARCHAR(120) NULL,
  created_at DATETIME     NULL,
  UNIQUE KEY uq_user_menu (user_code, menu_id),
  KEY idx_user (user_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
