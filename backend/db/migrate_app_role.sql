-- Role Master: admin-defined roles for the planning tool.
-- Run once by a DBA (the app's MySQL login is DML-only and cannot CREATE):
--   mysql -u root -p planning_tool < backend/db/migrate_app_role.sql
-- Until this runs, the app uses a JSON fallback (app_roles.json) automatically.

CREATE TABLE IF NOT EXISTS sc_app_role (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  role_name   VARCHAR(100) NOT NULL,
  description VARCHAR(400) NOT NULL DEFAULT '',
  active      TINYINT(1)   NOT NULL DEFAULT 1,
  created_by  VARCHAR(120) NOT NULL DEFAULT '',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_role_name (role_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed the default SRDMS roles (idempotent).
INSERT IGNORE INTO sc_app_role (role_name, description, active, created_by) VALUES
  ('R&D Requester',            'Raises sample-material requests',        1, 'migration'),
  ('Warehouse In-charge',      'Acknowledges / dispatches / holds',      1, 'migration'),
  ('Warehouse Executive',      'Warehouse dispatch executive',           1, 'migration'),
  ('QA / QC',                  'QA batch release / QC for R&D samples',  1, 'migration'),
  ('R&D Head / Plant Head',    'Approver / referred requests',           1, 'migration'),
  ('System Administrator',     'Full administrative access',             1, 'migration');
