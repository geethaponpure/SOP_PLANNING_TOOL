-- Migration: persist the user-chosen BOM variant per FG (BOM override).
-- Lets an overridden BOM flow into the saved plan, Excel and Production Scheduling.
-- planning_app has DML only (no ALTER), so run ONCE as root:
--   mysql -u root -p planning_tool < backend/db/migrate_bom_variant.sql

ALTER TABLE PLAN_FG_DEMAND ADD COLUMN bom_variant VARCHAR(80) NULL AFTER bom_class;
