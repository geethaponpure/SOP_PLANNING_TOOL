-- ---------------------------------------------------------------------------
-- Projection pages (Projection-Accuracy + Projection-vs-Sales) — the last
-- planning sources moved off request-time CRM. Run AFTER migrate_staging_p2b_b.sql.
--
--   stg_projection.approved  - projection is now staged per (acc_year, jc, approved)
--                              so the accuracy page's approved/unapproved toggle and
--                              its JC1..current year-to-date comparison work offline.
--   stg_projection_rows      - projection per item x collector (Projection-vs-Sales).
--
-- Run as root, or as planning_app with CREATE/ALTER granted.
-- ---------------------------------------------------------------------------

USE planning_tool;

-- Existing stg_projection rows default to approved=1 (they were the approved plan).
ALTER TABLE stg_projection
  ADD COLUMN IF NOT EXISTS approved TINYINT NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS stg_projection_rows (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  acc_year  VARCHAR(9)   NOT NULL,
  jc        INT          NOT NULL,
  item_name VARCHAR(255) NULL,
  collector VARCHAR(400) NULL,
  segment2  VARCHAR(64)  NULL,
  segment3  VARCHAR(64)  NULL,
  current_q DECIMAL(18,3) NOT NULL DEFAULT 0,
  next1_q   DECIMAL(18,3) NOT NULL DEFAULT 0,
  next2_q   DECIMAL(18,3) NOT NULL DEFAULT 0,
  KEY idx_ctx (acc_year, jc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
