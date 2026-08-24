-- Migration: audit log of user access changes (module grants, role, status, password).
-- Run as ROOT:
--   mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_user_access_log.sql"
-- Until this runs, access changes are logged to backend/user_master.json (JSON fallback).
USE planning_tool;

CREATE TABLE IF NOT EXISTS sc_user_access_log (
  id               BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
  logged_at        DATETIME     NOT NULL,
  target_user_code VARCHAR(32)  NULL,      -- the user whose access changed
  target_name      VARCHAR(200) NULL,
  action           VARCHAR(40)  NOT NULL,  -- approved | removed | status | grant | revoke | set_modules | role | password_set | password_reset
  detail           VARCHAR(400) NULL,      -- e.g. module id/label, old->new
  changed_by_code  VARCHAR(32)  NULL,      -- the admin who made the change
  changed_by_name  VARCHAR(120) NULL,
  KEY idx_target (target_user_code),
  KEY idx_time (logged_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
