-- Migration: BOM activity classification (Manufacturing vs Repack/Relabel).
-- Persists, per saved plan, whether each FG is Manufacturing / Repack-Relabel /
-- Unclassified, and tags each RM allocation with the activity it serves — so the
-- split is kept for future reference (audit, reporting, adhoc).
--
-- planning_app has DML only (no ALTER), so run this ONCE as root:
--   mysql -u root -p planning_tool < backend/db/migrate_activity.sql

ALTER TABLE PLAN_FG_DEMAND
  ADD COLUMN bom_class VARCHAR(20) NULL AFTER source;

ALTER TABLE RM_ALLOCATION_LEDGER
  ADD COLUMN activity VARCHAR(20) NULL AFTER plan_type;

ALTER TABLE PLAN_FG_DEMAND      ADD KEY idx_bomclass (bom_class);
ALTER TABLE RM_ALLOCATION_LEDGER ADD KEY idx_activity (activity);
