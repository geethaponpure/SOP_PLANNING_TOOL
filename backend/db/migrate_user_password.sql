-- Migration: add the login password column to an EXISTING sc_app_user table.
-- (Use this if you already ran migrate_user_master.sql before the password feature.)
-- Run as ROOT:
--     mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_user_password.sql"
-- Passwords are stored as a salted one-way hash (pbkdf2_sha256$...), never plaintext;
-- the app sets each user's default to 'pure@123' and hashes it.
USE planning_tool;

-- MySQL has no "ADD COLUMN IF NOT EXISTS"; if the column already exists this errors
-- harmlessly (1060 Duplicate column) — ignore it in that case.
ALTER TABLE sc_app_user ADD COLUMN password_hash VARCHAR(255) NULL AFTER status;
