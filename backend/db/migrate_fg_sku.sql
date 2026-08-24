-- Migration: add the admin-added Vooki FG SKU table.
-- Run as ROOT (needs CREATE privilege — planning_app only has DML):
--     mysql -u root -p -e "source C:/Users/yashb/sop-planning-tool/backend/db/migrate_fg_sku.sql"
-- or open this file in MySQL Workbench and Execute. Re-running setup.sql works too.
USE planning_tool;

CREATE TABLE IF NOT EXISTS vooki_fg_sku (
  sku_code   VARCHAR(64)  NOT NULL PRIMARY KEY,
  item_desc  VARCHAR(255) NOT NULL,
  source     VARCHAR(32)  NOT NULL DEFAULT 'manual',
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
               ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
