-- ---------------------------------------------------------------------------
-- RM Price Impact card: raw-material price movements from CRM's purchase
-- requisitions.
--
-- Source: PurchaseRequisitionDtls, which carries the PREVIOUS PO price
-- (lastpoprice) alongside the current one (unit_price) on every line, so a
-- price movement needs no reconstruction.
--
-- Two guards are applied AT SYNC, not at read time, so every consumer sees the
-- same cleaned set:
--   * lastpoprice <= 1.00 is a placeholder, not a price — dropped.
--   * a move beyond +/- MAX% is a unit change or a typing error, not a price
--     change (measured: 12 of 365 increases exceeded +100%, one claiming
--     Rs 162.86 -> Rs 2,565.00). Kept but FLAGGED via `implausible` so the card
--     can exclude them and still report how many were set aside.
--
-- CRM's own change_in_price_per column is deliberately NOT stored: it is
-- unsigned (a 4.8% DECREASE is recorded as 1.000), so the percentage is
-- computed here from the two prices instead.
--
-- Replaced in full each sync.  Run once:
--   mysql -u root -p planning_tool < migrate_rm_price.sql
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_rm_price_move (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_code    VARCHAR(64)   NOT NULL,
  item_name    VARCHAR(255)  NULL,
  old_price    DECIMAL(18,4) NOT NULL DEFAULT 0,   -- lastpoprice
  new_price    DECIMAL(18,4) NOT NULL DEFAULT 0,   -- unit_price on the requisition
  pct          DECIMAL(10,2) NOT NULL DEFAULT 0,   -- computed, signed
  moved_on     DATE          NULL,                 -- latest requisition date
  req_lines    INT           NOT NULL DEFAULT 0,
  req_qty      DECIMAL(18,3) NOT NULL DEFAULT 0,
  supplier     VARCHAR(255)  NULL,
  implausible  TINYINT(1)    NOT NULL DEFAULT 0,   -- outside the sane band
  UNIQUE KEY uq_item (item_code),
  KEY idx_pct (pct),
  KEY idx_moved (moved_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
