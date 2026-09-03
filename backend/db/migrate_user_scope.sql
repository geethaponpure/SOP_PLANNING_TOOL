-- ---------------------------------------------------------------------------
-- User data-scope staging ("the ultimate table") for the permission-based
-- dashboard page. One row per user x atomic data grant, flattened from SIX
-- CRM mapping sources into a single shape the API can filter on:
--
--   persona             source table                        grant
--   ------------------  ----------------------------------  ---------------------------
--   Sales Executive     UserMarketCircleMappings            market circle (mc_code)
--   Branch Manager      CollectorMailMappings.bm_user_id    collector
--   Regional Manager    CollectorMailMappings.rm_user_id    collectors (one row each)
--   Technical Executive UserCustomerMappings                customer
--   Technical Head      TechnicalUserSegmentMappings        segment4 + collector list
--   Technical Manager   TechnicalUserSegmentMappings        segment4 + collector list
--   Business Head       SpAlertSegmentWorkflowDtls          segment3 + collector list
--   Division Head       SpAlertSegmentWorkflowHdrs          segment2 (whole division)
--
-- CSV collector lists ('1042,34085,...') are EXPLODED to one row per collector
-- by the worker; collector_id NULL means "all collectors" for that grant.
-- Replaced in full each sync (same transactional pattern as the other stg_*).
--
-- Run once:  mysql -u root -p planning_tool < migrate_user_scope.sql
-- ---------------------------------------------------------------------------

USE planning_tool;

CREATE TABLE IF NOT EXISTS stg_user_scope (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id        BIGINT       NOT NULL,           -- CRM dbo.Users.line_id
  user_name      VARCHAR(160) NULL,
  username       VARCHAR(80)  NULL,
  email          VARCHAR(160) NULL,
  persona        VARCHAR(32)  NOT NULL,           -- one of the 8 roles above
  scope_type     VARCHAR(16)  NOT NULL,           -- market_circle | collector | customer | segment
  mc_code        VARCHAR(32)  NULL,               -- market_circle grants
  region         VARCHAR(32)  NULL,               -- region of the market circle
  collector_id   BIGINT       NULL,               -- NULL = all collectors
  collector_name VARCHAR(120) NULL,
  customer_id    BIGINT       NULL,               -- customer grants (CRM customer_id)
  customer_name  VARCHAR(255) NULL,
  segment2       VARCHAR(64)  NULL,               -- segment grants (division level)
  segment3       VARCHAR(64)  NULL,
  segment4       VARCHAR(64)  NULL,
  src            VARCHAR(48)  NOT NULL,           -- CRM table the grant came from
  KEY idx_user (user_id),
  KEY idx_persona (persona),
  KEY idx_collector (collector_id),
  KEY idx_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
