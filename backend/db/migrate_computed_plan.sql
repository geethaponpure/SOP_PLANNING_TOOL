-- ---------------------------------------------------------------------------
-- Phase 3 (see ARCHITECTURE.md) — precomputed plan store.
--
-- The worker runs the heavy RM-Plan build (planning_filter, BOM explosion) AFTER
-- each sync and stores the finished result JSON here; the API just SELECTs it, so
-- the RM-Plan page loads instantly instead of computing on the request.
--
--   computed_plan(plan_key) — 'rm_planning' is the default (no-override) plan.
-- Replaced (REPLACE INTO) each compute. Run as root, or planning_app with CREATE.
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS computed_plan (
  plan_key    VARCHAR(48) NOT NULL PRIMARY KEY,   -- 'rm_planning'
  payload     MEDIUMTEXT  NULL,                   -- JSON of the built plan
  n_products  INT         NULL,
  computed_at DATETIME    NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
