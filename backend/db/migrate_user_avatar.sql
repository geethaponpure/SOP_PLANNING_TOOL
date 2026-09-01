-- Migration: add the profile-avatar column to an EXISTING sc_app_user table.
-- Run as ROOT:
--     mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_user_avatar.sql"
-- Stores a short avatar id (e.g. 'doctor-female-brown') that maps to a bundled
-- 3D avatar image in the frontend. Until this runs, avatar choices persist to the
-- JSON fallback (backend/user_master.json) instead of MySQL.
USE planning_tool;

-- MySQL has no "ADD COLUMN IF NOT EXISTS"; if the column already exists this errors
-- harmlessly (1060 Duplicate column) — ignore it in that case.
ALTER TABLE sc_app_user ADD COLUMN avatar VARCHAR(64) NULL AFTER status;
