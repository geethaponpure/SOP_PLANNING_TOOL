-- ---------------------------------------------------------------------------
-- Commitment-Risk page: one row per OPEN sale-order schedule line (balance
-- still to dispatch), with every date the risk math needs.
--
--   sched_date    - the ORIGINAL committed delivery date (Schedules.SCHEDULE_DATE)
--   resched_date  - the CURRENT commitment (RESCHEDULE_DATE; = sched when never moved)
--   soc_date      - when the order was placed
--
-- Risk buckets (overdue / due today / due 48h / this week / later) are NOT
-- stored: they depend on "today", so the API derives them at read time.
-- Segments are denormalized from stg_item_segments at sync (like the dispatch
-- cube) so segment personas filter with plain indexed predicates.
--
-- Replaced in full each sync.  Run once:
--   mysql -u root -p planning_tool < migrate_commit.sql
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_order_commit (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_no       BIGINT        NULL,
  soc_line_id    BIGINT        NULL,
  order_ref      VARCHAR(40)   NULL,             -- quotation/order number shown to users
  soc_date       DATE          NULL,
  customer_id    BIGINT        NULL,
  customer_name  VARCHAR(255)  NULL,
  collector      VARCHAR(120)  NULL,
  mc_code        VARCHAR(32)   NULL,
  item_code      VARCHAR(64)   NULL,
  item_name      VARCHAR(255)  NULL,
  item_group     VARCHAR(120)  NULL,
  inv_org        VARCHAR(120)  NULL,
  sales_type     VARCHAR(32)   NULL,
  qty            DECIMAL(18,3) NOT NULL DEFAULT 0,
  despatched     DECIMAL(18,3) NOT NULL DEFAULT 0,
  balance        DECIMAL(18,3) NOT NULL DEFAULT 0,
  sched_date     DATE          NULL,
  resched_date   DATE          NULL,
  cust_req_date  DATE          NULL,             -- date the CUSTOMER asked for
  resched_reason VARCHAR(120)  NULL,
  wh_comments    VARCHAR(255)  NULL,             -- warehouse note on the reschedule
  executive      VARCHAR(120)  NULL,
  dispatch_pct   DECIMAL(6,2)  NULL,
  confirm_status VARCHAR(32)   NULL,
  segment2       VARCHAR(64)   NULL,
  segment3       VARCHAR(64)   NULL,
  segment4       VARCHAR(64)   NULL,
  KEY idx_collector (collector),
  KEY idx_mc (mc_code),
  KEY idx_customer (customer_id),
  KEY idx_resched (resched_date),
  KEY idx_seg3 (segment3),
  KEY idx_seg4 (segment4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
