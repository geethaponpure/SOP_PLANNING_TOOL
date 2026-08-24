-- Migration: persist PO receipts (from the PO_receipts/ folder) to the DB,
-- de-duplicated across overlapping periodic Oracle downloads by Receipt No.
-- planning_app has DML only (no CREATE), so run ONCE as root:
--   mysql -u root -p planning_tool < backend/db/migrate_po_receipts.sql

CREATE TABLE IF NOT EXISTS PO_RECEIPTS (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  receipt_no  VARCHAR(48)  NOT NULL DEFAULT '',
  po_number   VARCHAR(48)  NOT NULL DEFAULT '',
  item_code   VARCHAR(64)  NOT NULL DEFAULT '',
  item_desc   VARCHAR(255),
  po_date     DATE NULL,
  receipt_date DATE NULL,
  po_qty      DECIMAL(18,3) NOT NULL DEFAULT 0,
  receipt_qty DECIMAL(18,3) NOT NULL DEFAULT 0,
  vendor_name VARCHAR(255),
  org_name    VARCHAR(120),
  subinventory VARCHAR(64) NOT NULL DEFAULT '',
  lot_number  VARCHAR(80) NOT NULL DEFAULT '',
  currency    VARCHAR(8),
  unit_price  DECIMAL(18,4),
  ingested_at DATETIME NOT NULL,
  -- One Receipt No. can hold several lines for a PO+item (split by lot/sub-inv/qty),
  -- so the unique key includes them; else split receipts collapse and received qty is
  -- lost (see migrate_po_receipts_split_lines.sql for the fix on existing tables).
  UNIQUE KEY uq_receipt_line (receipt_no, po_number, item_code, lot_number, subinventory, receipt_qty),
  KEY idx_item (item_code),
  KEY idx_recdate (receipt_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
