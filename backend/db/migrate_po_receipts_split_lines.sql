-- Migration: widen the PO_RECEIPTS unique key so split receipt lines are preserved.
--
-- A single Receipt No. (GRN) can legitimately hold SEVERAL receipt lines for one PO+item
-- (split by lot / sub-inventory / quantity — e.g. GRN 2373 for PO 208572 booked as
-- 1,294 + 521,128 = the full 522,422). The old unique key (receipt_no, po_number,
-- item_code) collapsed them via ON DUPLICATE KEY UPDATE, so only the LAST line's
-- receipt_qty survived — under-stating received qty and over-stating in-transit.
--
-- planning_app has DML only (no DDL), so run ONCE as root:
--   mysql -u root -p planning_tool < backend/db/migrate_po_receipts_split_lines.sql
--
-- Idempotent-ish: safe to re-run except the DROP/ADD INDEX (skip if already applied).

-- 1. lot / subinventory take part in the unique key, so NULLs (treated as DISTINCT by
--    MySQL unique keys) must become '' to actually enforce uniqueness. The app already
--    inserts '' (never NULL) for these.
UPDATE PO_RECEIPTS SET lot_number  = '' WHERE lot_number  IS NULL;
UPDATE PO_RECEIPTS SET subinventory = '' WHERE subinventory IS NULL;

ALTER TABLE PO_RECEIPTS
  MODIFY lot_number   VARCHAR(80) NOT NULL DEFAULT '',
  MODIFY subinventory VARCHAR(64) NOT NULL DEFAULT '';

-- 2. Replace the coarse unique key with the split-line-aware one.
ALTER TABLE PO_RECEIPTS DROP INDEX uq_receipt;
ALTER TABLE PO_RECEIPTS
  ADD UNIQUE KEY uq_receipt_line
  (receipt_no, po_number, item_code, lot_number, subinventory, receipt_qty);

-- 3. Backfill note: rows dropped by the old key are NOT auto-restored. Re-ingest to
--    add them — drop a fresh PO register into PO_receipts/ (the app's _ensure_po_fresh
--    re-ingests on any folder change), or clear the app .cache and rebuild the RM plan.
