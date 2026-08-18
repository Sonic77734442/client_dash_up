import hmac
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from uuid import UUID, uuid4


class IntegrationCredentialConfigurationError(RuntimeError):
    """The credential scope identity is ambiguous and startup must stop.

    Credential rows can contain live provider grants, so startup migrations
    deliberately never pick a winner or delete duplicates.  Operators can
    resolve the duplicate rows explicitly and restart the application.
    """

    code = "integration_credential_scope_duplicates"

    def __init__(self, duplicate_groups: int):
        self.duplicate_groups = max(int(duplicate_groups), 1)
        super().__init__(
            "Integration credential scope identities are duplicated; "
            "no credential was modified. Resolve the duplicate rows before restart."
        )


DDL = """
CREATE TABLE IF NOT EXISTS clients (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  legal_name TEXT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','archived')),
  default_currency TEXT NOT NULL DEFAULT 'USD',
  timezone TEXT NULL,
  notes TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ad_accounts (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  external_account_id TEXT NOT NULL,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  timezone TEXT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','archived')),
  metadata TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_accounts_client_platform_external_id
ON ad_accounts(client_id, platform, external_account_id);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_client_id ON ad_accounts(client_id);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_status ON ad_accounts(status);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_assignment
ON ad_accounts(platform, external_account_id, status);

-- Existing production databases may already contain cross-client duplicates.  A
-- global unique index would fail to install and would force a destructive data
-- migration.  These triggers preserve existing rows while preventing any new
-- second *active* assignment of the same provider account to another client.
CREATE TRIGGER IF NOT EXISTS trg_ad_accounts_active_assignment_insert
BEFORE INSERT ON ad_accounts
WHEN NEW.status = 'active'
 AND EXISTS (
   SELECT 1
   FROM ad_accounts a
   WHERE (CASE WHEN lower(a.platform) IN ('meta', 'facebook') THEN 'meta' ELSE lower(a.platform) END)
       = (CASE WHEN lower(NEW.platform) IN ('meta', 'facebook') THEN 'meta' ELSE lower(NEW.platform) END)
     AND (
       CASE
         WHEN lower(a.platform) IN ('meta', 'facebook') THEN
           CASE WHEN lower(trim(a.external_account_id)) GLOB 'act_*'
             THEN substr(trim(a.external_account_id), 5) ELSE trim(a.external_account_id) END
         WHEN lower(a.platform) IN ('google', 'tiktok') THEN
           replace(replace(replace(replace(trim(a.external_account_id), '-', ''), ' ', ''), '_', ''), '.', '')
         ELSE trim(a.external_account_id)
       END
     ) = (
       CASE
         WHEN lower(NEW.platform) IN ('meta', 'facebook') THEN
           CASE WHEN lower(trim(NEW.external_account_id)) GLOB 'act_*'
             THEN substr(trim(NEW.external_account_id), 5) ELSE trim(NEW.external_account_id) END
         WHEN lower(NEW.platform) IN ('google', 'tiktok') THEN
           replace(replace(replace(replace(trim(NEW.external_account_id), '-', ''), ' ', ''), '_', ''), '.', '')
         ELSE trim(NEW.external_account_id)
       END
     )
     AND a.status = 'active'
     AND a.client_id <> NEW.client_id
     AND EXISTS (SELECT 1 FROM clients c WHERE c.id=a.client_id AND c.status='active')
 )
BEGIN
  SELECT RAISE(ABORT, 'assignment_conflict');
END;

CREATE TRIGGER IF NOT EXISTS trg_ad_accounts_active_assignment_update
BEFORE UPDATE ON ad_accounts
WHEN NEW.status = 'active'
 AND (
   OLD.status <> 'active'
   OR OLD.client_id <> NEW.client_id
   OR lower(OLD.platform) <> lower(NEW.platform)
   OR OLD.external_account_id <> NEW.external_account_id
 )
 AND EXISTS (
   SELECT 1
   FROM ad_accounts a
   WHERE a.id <> OLD.id
     AND (CASE WHEN lower(a.platform) IN ('meta', 'facebook') THEN 'meta' ELSE lower(a.platform) END)
       = (CASE WHEN lower(NEW.platform) IN ('meta', 'facebook') THEN 'meta' ELSE lower(NEW.platform) END)
     AND (
       CASE
         WHEN lower(a.platform) IN ('meta', 'facebook') THEN
           CASE WHEN lower(trim(a.external_account_id)) GLOB 'act_*'
             THEN substr(trim(a.external_account_id), 5) ELSE trim(a.external_account_id) END
         WHEN lower(a.platform) IN ('google', 'tiktok') THEN
           replace(replace(replace(replace(trim(a.external_account_id), '-', ''), ' ', ''), '_', ''), '.', '')
         ELSE trim(a.external_account_id)
       END
     ) = (
       CASE
         WHEN lower(NEW.platform) IN ('meta', 'facebook') THEN
           CASE WHEN lower(trim(NEW.external_account_id)) GLOB 'act_*'
             THEN substr(trim(NEW.external_account_id), 5) ELSE trim(NEW.external_account_id) END
         WHEN lower(NEW.platform) IN ('google', 'tiktok') THEN
           replace(replace(replace(replace(trim(NEW.external_account_id), '-', ''), ' ', ''), '_', ''), '.', '')
         ELSE trim(NEW.external_account_id)
       END
     )
     AND a.status = 'active'
     AND a.client_id <> NEW.client_id
     AND EXISTS (SELECT 1 FROM clients c WHERE c.id=a.client_id AND c.status='active')
 )
BEGIN
  SELECT RAISE(ABORT, 'assignment_conflict');
END;

-- Restoring an archived client can reactivate preserved child mappings. Guard
-- that transition in the database as well, so a concurrent account assignment
-- cannot race the application-level restore check.
CREATE TRIGGER IF NOT EXISTS trg_clients_active_assignment_restore
BEFORE UPDATE OF status ON clients
WHEN NEW.status = 'active'
 AND OLD.status <> 'active'
 AND EXISTS (
   SELECT 1
   FROM ad_accounts own
   JOIN ad_accounts other
     ON other.client_id <> NEW.id
    AND other.status = 'active'
   JOIN clients other_client
     ON other_client.id = other.client_id
    AND other_client.status = 'active'
   WHERE own.client_id = NEW.id
     AND own.status = 'active'
     AND (CASE WHEN lower(own.platform) IN ('meta', 'facebook') THEN 'meta' ELSE lower(own.platform) END)
       = (CASE WHEN lower(other.platform) IN ('meta', 'facebook') THEN 'meta' ELSE lower(other.platform) END)
     AND (
       CASE
         WHEN lower(own.platform) IN ('meta', 'facebook') THEN
           CASE WHEN lower(trim(own.external_account_id)) GLOB 'act_*'
             THEN substr(trim(own.external_account_id), 5) ELSE trim(own.external_account_id) END
         WHEN lower(own.platform) IN ('google', 'tiktok') THEN
           replace(replace(replace(replace(trim(own.external_account_id), '-', ''), ' ', ''), '_', ''), '.', '')
         ELSE trim(own.external_account_id)
       END
     ) = (
       CASE
         WHEN lower(other.platform) IN ('meta', 'facebook') THEN
           CASE WHEN lower(trim(other.external_account_id)) GLOB 'act_*'
             THEN substr(trim(other.external_account_id), 5) ELSE trim(other.external_account_id) END
         WHEN lower(other.platform) IN ('google', 'tiktok') THEN
           replace(replace(replace(replace(trim(other.external_account_id), '-', ''), ' ', ''), '_', ''), '.', '')
         ELSE trim(other.external_account_id)
       END
     )
 )
BEGIN
  SELECT RAISE(ABORT, 'assignment_conflict');
END;

CREATE TABLE IF NOT EXISTS ad_account_sync_jobs (
  id TEXT PRIMARY KEY,
  ad_account_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success','error')),
  started_at TEXT NOT NULL,
  finished_at TEXT NULL,
  records_synced INTEGER NOT NULL DEFAULT 0,
  error_message TEXT NULL,
  error_code TEXT NULL,
  error_category TEXT NULL,
  retryable INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 1,
  next_retry_at TEXT NULL,
  request_meta TEXT NULL,
  created_by TEXT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (ad_account_id) REFERENCES ad_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_ad_account_sync_jobs_account ON ad_account_sync_jobs(ad_account_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ad_account_sync_jobs_status ON ad_account_sync_jobs(status, started_at DESC);

CREATE TABLE IF NOT EXISTS ad_account_sync_leases (
  lease_key TEXT PRIMARY KEY,
  lease_token TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ad_stats (
  id TEXT PRIMARY KEY,
  ad_account_id TEXT NOT NULL,
  date TEXT NOT NULL,
  platform TEXT NOT NULL,
  impressions INTEGER NOT NULL DEFAULT 0,
  clicks INTEGER NOT NULL DEFAULT 0,
  spend NUMERIC NOT NULL,
  conversions NUMERIC NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (ad_account_id) REFERENCES ad_accounts(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_stats_account_date_platform ON ad_stats(ad_account_id, date, platform);
CREATE INDEX IF NOT EXISTS idx_ad_stats_date ON ad_stats(date);
CREATE INDEX IF NOT EXISTS idx_ad_stats_platform ON ad_stats(platform);

CREATE TABLE IF NOT EXISTS ad_stats_ingest_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  request_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','agency','client','solo_client')),
  password_hash TEXT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_identities (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_user_id TEXT NOT NULL,
  email TEXT NULL,
  email_verified INTEGER NULL,
  raw_profile TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider, provider_user_id),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_client_access (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('agency','client')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, client_id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS agencies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  plan TEXT NOT NULL DEFAULT 'starter',
  notes TEXT NULL,
  allow_client_invites INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agency_members (
  id TEXT PRIMARY KEY,
  agency_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','manager','member')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(agency_id, user_id),
  FOREIGN KEY (agency_id) REFERENCES agencies(id),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS agency_client_access (
  id TEXT PRIMARY KEY,
  agency_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(agency_id, client_id),
  FOREIGN KEY (agency_id) REFERENCES agencies(id),
  FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS agency_invites (
  id TEXT PRIMARY KEY,
  agency_id TEXT NOT NULL,
  email TEXT NOT NULL,
  member_role TEXT NOT NULL DEFAULT 'member' CHECK (member_role IN ('owner','manager','member')),
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','revoked','expired')),
  expires_at TEXT NOT NULL,
  invited_by TEXT NULL,
  accepted_user_id TEXT NULL,
  accepted_at TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (agency_id) REFERENCES agencies(id),
  FOREIGN KEY (invited_by) REFERENCES users(id),
  FOREIGN KEY (accepted_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS client_invites (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  email TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','revoked','expired')),
  expires_at TEXT NOT NULL,
  invited_by TEXT NULL,
  accepted_user_id TEXT NULL,
  accepted_at TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (client_id) REFERENCES clients(id),
  FOREIGN KEY (invited_by) REFERENCES users(id),
  FOREIGN KEY (accepted_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  revoked_at TEXT NULL,
  metadata TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  next_path TEXT NULL,
  nonce TEXT NOT NULL DEFAULT '',
  initiator_user_id TEXT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_provider_configs (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL UNIQUE,
  client_id TEXT NOT NULL,
  client_secret TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_credentials (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('global','agency','client')),
  scope_id TEXT NULL,
  connection_key TEXT NOT NULL DEFAULT 'default',
  credentials_json TEXT NOT NULL,
  semantic_revision INTEGER NOT NULL DEFAULT 1 CHECK (semantic_revision > 0),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_by TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((scope_type='global' AND scope_id IS NULL) OR (scope_type IN ('agency','client') AND scope_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_auth_identities_user_id ON auth_identities(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_identities_provider ON auth_identities(provider);
CREATE INDEX IF NOT EXISTS idx_user_client_access_user_id ON user_client_access(user_id);
CREATE INDEX IF NOT EXISTS idx_user_client_access_client_id ON user_client_access(client_id);
CREATE INDEX IF NOT EXISTS idx_agency_members_agency_id ON agency_members(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_members_user_id ON agency_members(user_id);
CREATE INDEX IF NOT EXISTS idx_agency_client_access_agency_id ON agency_client_access(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_client_access_client_id ON agency_client_access(client_id);
CREATE INDEX IF NOT EXISTS idx_agency_invites_agency_id ON agency_invites(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_invites_email ON agency_invites(email);
CREATE INDEX IF NOT EXISTS idx_agency_invites_status ON agency_invites(status);
CREATE INDEX IF NOT EXISTS idx_client_invites_client_id ON client_invites(client_id);
CREATE INDEX IF NOT EXISTS idx_client_invites_email ON client_invites(email);
CREATE INDEX IF NOT EXISTS idx_client_invites_status ON client_invites(status);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_states_provider ON oauth_states(provider, created_at);

CREATE TABLE IF NOT EXISTS budgets (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'client' CHECK (scope IN ('client','account')),
  account_id TEXT NULL,
  amount NUMERIC NOT NULL CHECK (amount >= 0),
  currency TEXT NOT NULL DEFAULT 'USD',
  period_type TEXT NOT NULL CHECK (period_type IN ('monthly','custom')),
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  version INTEGER NOT NULL DEFAULT 1,
  note TEXT NULL,
  created_by TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((scope='client' AND account_id IS NULL) OR (scope='account' AND account_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS budget_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  budget_id TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  changed_by TEXT NULL,
  previous_values TEXT NOT NULL,
  new_values TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_transfers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_budget_id TEXT NOT NULL,
  target_budget_id TEXT NOT NULL,
  amount NUMERIC NOT NULL CHECK (amount > 0),
  note TEXT NULL,
  changed_by TEXT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_budgets_client_id ON budgets(client_id);
CREATE INDEX IF NOT EXISTS idx_budgets_account_id ON budgets(account_id);
CREATE INDEX IF NOT EXISTS idx_budgets_client_period ON budgets(client_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_budgets_scope ON budgets(scope);
CREATE INDEX IF NOT EXISTS idx_budget_history_budget_id ON budget_history(budget_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_budget_transfers_source ON budget_transfers(source_budget_id, created_at);
CREATE INDEX IF NOT EXISTS idx_budget_transfers_target ON budget_transfers(target_budget_id, created_at);

-- Durable command ledger for provider-side budget mutations.  A provider
-- write is never represented by the planning `budgets` table: this ledger
-- records the exact tenant, account, credential, actor and preview proof that
-- authorized each external mutation attempt.
CREATE TABLE IF NOT EXISTS provider_budget_commands (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL CHECK (provider IN ('meta')),
  client_id TEXT NOT NULL,
  agency_id TEXT NULL,
  ad_account_id TEXT NOT NULL,
  provider_account_id TEXT NOT NULL,
  credential_id TEXT NOT NULL,
  actor_user_id TEXT NOT NULL,
  authorization_snapshot_json TEXT NOT NULL,
  credential_snapshot_json TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('campaign','ad_set','account')),
  provider_target_id TEXT NOT NULL,
  budget_field TEXT NOT NULL CHECK (budget_field IN ('daily_budget','lifetime_budget','spend_cap')),
  amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
  currency TEXT NOT NULL,
  expected_current_minor INTEGER NULL CHECK (expected_current_minor IS NULL OR expected_current_minor >= 0),
  reason TEXT NOT NULL,
  source_action_id TEXT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','in_progress','applied','conflict','failed','unknown')),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  preview_hash TEXT NOT NULL,
  observed_before_minor INTEGER NOT NULL CHECK (observed_before_minor >= 0),
  confirmed_after_minor INTEGER NULL CHECK (confirmed_after_minor IS NULL OR confirmed_after_minor >= 0),
  provider_trace_id TEXT NULL,
  error_code TEXT NULL,
  error_message TEXT NULL,
  error_subcode TEXT NULL,
  error_retryable INTEGER NOT NULL DEFAULT 0,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT NULL,
  UNIQUE(client_id, idempotency_key),
  FOREIGN KEY (client_id) REFERENCES clients(id),
  FOREIGN KEY (agency_id) REFERENCES agencies(id),
  FOREIGN KEY (ad_account_id) REFERENCES ad_accounts(id),
  FOREIGN KEY (credential_id) REFERENCES integration_credentials(id),
  FOREIGN KEY (actor_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS provider_budget_command_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('applied','conflict','failed','unknown')),
  observed_before_minor INTEGER NULL CHECK (observed_before_minor IS NULL OR observed_before_minor >= 0),
  confirmed_after_minor INTEGER NULL CHECK (confirmed_after_minor IS NULL OR confirmed_after_minor >= 0),
  provider_trace_id TEXT NULL,
  error_code TEXT NULL,
  error_message TEXT NULL,
  error_subcode TEXT NULL,
  error_retryable INTEGER NOT NULL DEFAULT 0,
  reconciliation INTEGER NOT NULL DEFAULT 0 CHECK (reconciliation IN (0,1)),
  actor_user_id TEXT NULL,
  credential_id TEXT NULL,
  authorization_snapshot_json TEXT NOT NULL,
  credential_snapshot_json TEXT NOT NULL,
  UNIQUE(command_id, attempt_no),
  FOREIGN KEY (command_id) REFERENCES provider_budget_commands(id),
  FOREIGN KEY (actor_user_id) REFERENCES users(id),
  FOREIGN KEY (credential_id) REFERENCES integration_credentials(id)
);

CREATE TABLE IF NOT EXISTS provider_budget_target_locks (
  provider TEXT NOT NULL,
  target_type TEXT NOT NULL,
  provider_target_id TEXT NOT NULL,
  budget_field TEXT NOT NULL,
  command_id TEXT NOT NULL,
  lease_token TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (provider, target_type, provider_target_id, budget_field),
  FOREIGN KEY (command_id) REFERENCES provider_budget_commands(id)
);

-- UNKNOWN means a POST may have reached Meta.  Keep a durable target
-- quarantine after the short execution lease is released so a fresh preview
-- cannot authorize a second, potentially duplicate mutation.  Only a
-- conclusive read-only reconciliation removes this row.
CREATE TABLE IF NOT EXISTS provider_budget_target_quarantines (
  provider TEXT NOT NULL,
  target_type TEXT NOT NULL,
  provider_target_id TEXT NOT NULL,
  budget_field TEXT NOT NULL,
  command_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (provider, target_type, provider_target_id, budget_field),
  FOREIGN KEY (command_id) REFERENCES provider_budget_commands(id)
);

CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_client_created
ON provider_budget_commands(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_account_created
ON provider_budget_commands(ad_account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_actor_created
ON provider_budget_commands(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_status_updated
ON provider_budget_commands(status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_budget_commands_preview_hash
ON provider_budget_commands(preview_hash);
CREATE INDEX IF NOT EXISTS idx_provider_budget_attempts_command
ON provider_budget_command_attempts(command_id, attempt_no);
CREATE INDEX IF NOT EXISTS idx_provider_budget_target_quarantines_command
ON provider_budget_target_quarantines(command_id);

-- CHECK constraints above protect newly-created databases. These triggers add
-- the same invariant to existing SQLite files, whose table-level constraints
-- cannot be changed in place without rebuilding the table.
CREATE TRIGGER IF NOT EXISTS trg_budgets_nonnegative_insert
BEFORE INSERT ON budgets
WHEN CAST(NEW.amount AS NUMERIC) < 0
BEGIN
  SELECT RAISE(ABORT, 'budget_amount_negative');
END;

CREATE TRIGGER IF NOT EXISTS trg_budgets_nonnegative_update
BEFORE UPDATE OF amount ON budgets
WHEN CAST(NEW.amount AS NUMERIC) < 0
BEGIN
  SELECT RAISE(ABORT, 'budget_amount_negative');
END;

CREATE TRIGGER IF NOT EXISTS trg_budget_transfers_positive_insert
BEFORE INSERT ON budget_transfers
WHEN CAST(NEW.amount AS NUMERIC) <= 0
BEGIN
  SELECT RAISE(ABORT, 'budget_transfer_amount_not_positive');
END;

CREATE TABLE IF NOT EXISTS operational_actions (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL CHECK (action IN ('scale','cap','pause','review')),
  scope TEXT NOT NULL CHECK (scope IN ('account','client','agency')),
  scope_id TEXT NOT NULL,
  title TEXT NOT NULL,
  reason TEXT NOT NULL,
  metrics TEXT NOT NULL,
  client_id TEXT NULL,
  account_id TEXT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','applied','failed')),
  created_by TEXT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operational_actions_created_at ON operational_actions(created_at);
CREATE INDEX IF NOT EXISTS idx_operational_actions_client_id ON operational_actions(client_id);
CREATE INDEX IF NOT EXISTS idx_operational_actions_account_id ON operational_actions(account_id);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NULL,
  actor_user_id TEXT NULL,
  actor_role TEXT NULL,
  tenant_client_id TEXT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user_id ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_client_id ON audit_logs(tenant_client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
  status TEXT NOT NULL CHECK (status IN ('open','acked','resolved')),
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  provider TEXT NULL,
  client_id TEXT NULL,
  ad_account_id TEXT NULL,
  context_json TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  acknowledged_at TEXT NULL,
  acknowledged_by TEXT NULL,
  resolved_at TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_status_severity ON alerts(status, severity, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_provider_client ON alerts(provider, client_id, last_seen_at DESC);
"""


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if user_cols and "password_hash" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NULL")
        conn.commit()

    users_table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    users_table_sql = str(users_table_sql_row[0] or "") if users_table_sql_row else ""
    if users_table_sql and "solo_client" not in users_table_sql:
        # SQLite cannot alter a CHECK constraint in place. Rebuild only the
        # users table, preserving identifiers and password hashes, so existing
        # production databases can accept the new role without changing any
        # tenant/access relationships.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Re-check under the write lock: another process may have completed
            # the same startup migration while this process was waiting.
            locked_sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            locked_sql = str(locked_sql_row[0] or "") if locked_sql_row else ""
            if locked_sql and "solo_client" not in locked_sql:
                conn.execute(
                    """
                    CREATE TABLE users_solo_role_migration (
                      id TEXT PRIMARY KEY,
                      email TEXT NULL UNIQUE,
                      name TEXT NOT NULL,
                      role TEXT NOT NULL CHECK (role IN ('admin','agency','client','solo_client')),
                      password_hash TEXT NULL,
                      status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO users_solo_role_migration
                      (id, email, name, role, password_hash, status, created_at, updated_at)
                    SELECT id, email, name, role, password_hash, status, created_at, updated_at
                    FROM users
                    """
                )
                conn.execute("DROP TABLE users")
                conn.execute("ALTER TABLE users_solo_role_migration RENAME TO users")
                foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
                if foreign_key_errors:
                    raise sqlite3.IntegrityError(
                        f"users role migration left {len(foreign_key_errors)} foreign-key violation(s)"
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(budgets)").fetchall()}
    if cols and "scope" not in cols:
        conn.execute("ALTER TABLE budgets ADD COLUMN scope TEXT NOT NULL DEFAULT 'client'")
        conn.execute("UPDATE budgets SET scope=CASE WHEN account_id IS NULL THEN 'client' ELSE 'account' END")

    oauth_cols = {row[1] for row in conn.execute("PRAGMA table_info(oauth_states)").fetchall()}
    if oauth_cols and "nonce" not in oauth_cols:
        conn.execute("ALTER TABLE oauth_states ADD COLUMN nonce TEXT NOT NULL DEFAULT ''")
    if oauth_cols and "initiator_user_id" not in oauth_cols:
        conn.execute("ALTER TABLE oauth_states ADD COLUMN initiator_user_id TEXT NULL")

    agency_cols = {row[1] for row in conn.execute("PRAGMA table_info(agencies)").fetchall()}
    if agency_cols and "allow_client_invites" not in agency_cols:
        conn.execute("ALTER TABLE agencies ADD COLUMN allow_client_invites INTEGER NOT NULL DEFAULT 1")

    sync_cols = {row[1] for row in conn.execute("PRAGMA table_info(ad_account_sync_jobs)").fetchall()}
    if sync_cols and "error_code" not in sync_cols:
        conn.execute("ALTER TABLE ad_account_sync_jobs ADD COLUMN error_code TEXT NULL")
    if sync_cols and "error_category" not in sync_cols:
        conn.execute("ALTER TABLE ad_account_sync_jobs ADD COLUMN error_category TEXT NULL")
    if sync_cols and "retryable" not in sync_cols:
        conn.execute("ALTER TABLE ad_account_sync_jobs ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0")
    if sync_cols and "attempt" not in sync_cols:
        conn.execute("ALTER TABLE ad_account_sync_jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1")
    if sync_cols and "next_retry_at" not in sync_cols:
        conn.execute("ALTER TABLE ad_account_sync_jobs ADD COLUMN next_retry_at TEXT NULL")

    # Multi-tenant isolation: ad account identity must be unique only inside client scope.
    conn.execute("DROP INDEX IF EXISTS uq_ad_accounts_platform_external_id")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_accounts_client_platform_external_id "
        "ON ad_accounts(client_id, platform, external_account_id)"
    )

    cred_cols = {row[1] for row in conn.execute("PRAGMA table_info(integration_credentials)").fetchall()}
    if cred_cols and "connection_key" not in cred_cols:
        conn.execute("ALTER TABLE integration_credentials ADD COLUMN connection_key TEXT NOT NULL DEFAULT 'default'")
    if cred_cols and "status" not in cred_cols:
        conn.execute("ALTER TABLE integration_credentials ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if cred_cols and "created_by" not in cred_cols:
        conn.execute("ALTER TABLE integration_credentials ADD COLUMN created_by TEXT NULL")
    if cred_cols and "semantic_revision" not in cred_cols:
        conn.execute(
            "ALTER TABLE integration_credentials ADD COLUMN semantic_revision INTEGER NOT NULL DEFAULT 1"
        )

    # A conventional UNIQUE index does not consider NULL equal to NULL, so it
    # never protected global credentials.  Do not guess which live grant wins
    # when upgrading a database that already contains duplicate identities.
    # Fail before replacing the old index and leave every credential intact.
    duplicate_credential_groups = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM (
              SELECT provider, scope_type, scope_id, connection_key
              FROM integration_credentials
              WHERE (scope_type='global' AND scope_id IS NULL)
                 OR (scope_type IN ('agency','client') AND scope_id IS NOT NULL)
              GROUP BY provider, scope_type, scope_id, connection_key
              HAVING COUNT(*) > 1
            ) duplicates
            """
        ).fetchone()[0]
    )
    if duplicate_credential_groups:
        raise IntegrationCredentialConfigurationError(duplicate_credential_groups)

    conn.execute("DROP INDEX IF EXISTS uq_integration_credentials_scope_provider")
    conn.execute("DROP INDEX IF EXISTS uq_integration_credentials_scope_provider_connection")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_global_connection "
        "ON integration_credentials(provider, connection_key) "
        "WHERE scope_type='global' AND scope_id IS NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_scoped_connection "
        "ON integration_credentials(provider, scope_type, scope_id, connection_key) "
        "WHERE scope_type IN ('agency','client') AND scope_id IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_integration_credentials_status "
        "ON integration_credentials(status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_integration_credentials_scope "
        "ON integration_credentials(scope_type, scope_id, status)"
    )

    attempt_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(provider_budget_command_attempts)").fetchall()
    }
    if attempt_cols and "actor_user_id" not in attempt_cols:
        conn.execute("ALTER TABLE provider_budget_command_attempts ADD COLUMN actor_user_id TEXT NULL")
    if attempt_cols and "credential_id" not in attempt_cols:
        conn.execute("ALTER TABLE provider_budget_command_attempts ADD COLUMN credential_id TEXT NULL")
    if attempt_cols and "authorization_snapshot_json" not in attempt_cols:
        conn.execute(
            "ALTER TABLE provider_budget_command_attempts "
            "ADD COLUMN authorization_snapshot_json TEXT NOT NULL "
            "DEFAULT '{\"snapshot_version\":0,\"legacy_unverified\":true}'"
        )
    if attempt_cols and "credential_snapshot_json" not in attempt_cols:
        conn.execute(
            "ALTER TABLE provider_budget_command_attempts "
            "ADD COLUMN credential_snapshot_json TEXT NOT NULL "
            "DEFAULT '{\"snapshot_version\":0,\"legacy_unverified\":true}'"
        )
    command_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(provider_budget_commands)").fetchall()
    }
    if command_cols and "authorization_snapshot_json" not in command_cols:
        conn.execute(
            "ALTER TABLE provider_budget_commands "
            "ADD COLUMN authorization_snapshot_json TEXT NOT NULL "
            "DEFAULT '{\"snapshot_version\":0,\"legacy_unverified\":true}'"
        )
    if command_cols and "credential_snapshot_json" not in command_cols:
        conn.execute(
            "ALTER TABLE provider_budget_commands "
            "ADD COLUMN credential_snapshot_json TEXT NOT NULL "
            "DEFAULT '{\"snapshot_version\":0,\"legacy_unverified\":true}'"
        )


def init_sqlite(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(DDL)
        _migrate_sqlite(conn)
        # The assignment marker is not an ACL level. Reconcile legacy agency
        # rows against the active membership/binding graph instead of merely
        # relabelling an orphan grant and leaving full write scope in place.
        conn.execute(
            """
            DELETE FROM user_client_access
            WHERE EXISTS (
              SELECT 1
              FROM users u
              WHERE u.id=user_client_access.user_id
                AND (
                  u.role='admin'
                  OR (
                    u.role='agency'
                    AND (
                      u.status<>'active'
                      OR NOT EXISTS (
                        SELECT 1
                        FROM agency_members am
                        JOIN agencies a ON a.id=am.agency_id
                        JOIN agency_client_access aca ON aca.agency_id=am.agency_id
                        WHERE am.user_id=user_client_access.user_id
                          AND aca.client_id=user_client_access.client_id
                          AND am.status='active'
                          AND a.status='active'
                      )
                    )
                  )
                )
            )
            """
        )
        conn.execute(
            """
            UPDATE user_client_access
            SET role = CASE
              WHEN user_id IN (SELECT id FROM users WHERE role='agency') THEN 'agency'
              ELSE 'client'
            END
            WHERE role <> CASE
              WHEN user_id IN (SELECT id FROM users WHERE role='agency') THEN 'agency'
              ELSE 'client'
            END
            """
        )
        conn.commit()


def sqlite_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def provider_budget_retention_summary(
    conn: sqlite3.Connection,
    *,
    actor_user_id: Optional[UUID] = None,
    agency_id: Optional[UUID] = None,
    credential_id: Optional[UUID] = None,
) -> dict[str, int]:
    """Return count-only immutable-ledger references for hard-delete guards."""
    supplied = sum(value is not None for value in (actor_user_id, agency_id, credential_id))
    if supplied != 1:
        raise ValueError("exactly one provider budget retention identity is required")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_budget_commands'"
    ).fetchone()
    if not exists:
        return {"history_count": 0, "unresolved_count": 0}
    if actor_user_id is not None:
        where = """
          c.actor_user_id=? OR EXISTS (
            SELECT 1 FROM provider_budget_command_attempts a
            WHERE a.command_id=c.id AND a.actor_user_id=?
          )
        """
        params: tuple[object, ...] = (str(actor_user_id), str(actor_user_id))
    elif agency_id is not None:
        where = """
          c.agency_id=? OR c.credential_id IN (
            SELECT ic.id FROM integration_credentials ic
            WHERE ic.scope_type='agency' AND ic.scope_id=?
          )
        """
        params = (str(agency_id), str(agency_id))
    else:
        where = "c.credential_id=?"
        params = (str(credential_id),)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS history_count,
               SUM(CASE WHEN c.status IN ('queued','in_progress','unknown') THEN 1 ELSE 0 END)
                 AS unresolved_count
        FROM provider_budget_commands c
        WHERE {where}
        """,
        params,
    ).fetchone()
    return {
        "history_count": int(row["history_count"] or 0),
        "unresolved_count": int(row["unresolved_count"] or 0),
    }


class SqliteProviderBudgetCommandStore:
    """Durable, fail-closed ledger for provider budget commands.

    The provider command domain deliberately owns validation and state-machine
    semantics.  This adapter only makes those transitions atomic and durable.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _target_key(request) -> tuple[str, str, str, str]:
        return ("meta", request.target_type.value, request.provider_target_id, request.field.value)

    @staticmethod
    def _upsert_target_quarantine(conn: sqlite3.Connection, row, *, now_iso: str) -> None:
        conn.execute(
            """
            INSERT INTO provider_budget_target_quarantines (
              provider, target_type, provider_target_id, budget_field,
              command_id, created_at, updated_at
            ) VALUES ('meta', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, target_type, provider_target_id, budget_field)
            DO UPDATE SET updated_at=excluded.updated_at
            WHERE provider_budget_target_quarantines.command_id=excluded.command_id
            """,
            (
                str(row["target_type"]),
                str(row["provider_target_id"]),
                str(row["budget_field"]),
                str(row["id"]),
                now_iso,
                now_iso,
            ),
        )

    @staticmethod
    def _dt(value: Optional[str]) -> Optional[datetime]:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _snapshot_json(snapshot) -> str:
        return json.dumps(
            snapshot.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @staticmethod
    def _snapshot_payload(value: object) -> dict:
        try:
            payload = json.loads(str(value or "{}"))
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _error_from_row(row, prefix: str = "error"):
        from app.services.provider_budget_commands import SanitizedProviderError

        code = row[f"{prefix}_code"]
        if not code:
            return None
        return SanitizedProviderError(
            code=str(code),
            message=str(row[f"{prefix}_message"] or "Provider request failed"),
            subcode=str(row[f"{prefix}_subcode"]) if row[f"{prefix}_subcode"] is not None else None,
            trace_id=str(row["provider_trace_id"]) if row["provider_trace_id"] else None,
            retryable=bool(row[f"{prefix}_retryable"]),
        )

    def _attempts(self, conn: sqlite3.Connection, command_id: UUID):
        from app.services.provider_budget_commands import (
            authorization_snapshot_from_payload,
            BudgetCommandAttempt,
            BudgetCommandStatus,
            credential_snapshot_from_payload,
            SanitizedProviderError,
        )

        rows = conn.execute(
            "SELECT * FROM provider_budget_command_attempts WHERE command_id=? ORDER BY attempt_no ASC",
            (str(command_id),),
        ).fetchall()
        attempts = []
        for row in rows:
            error = None
            if row["error_code"]:
                error = SanitizedProviderError(
                    code=str(row["error_code"]),
                    message=str(row["error_message"] or "Provider request failed"),
                    subcode=str(row["error_subcode"]) if row["error_subcode"] is not None else None,
                    trace_id=str(row["provider_trace_id"]) if row["provider_trace_id"] else None,
                    retryable=bool(row["error_retryable"]),
                )
            attempts.append(
                BudgetCommandAttempt(
                    attempt_no=int(row["attempt_no"]),
                    started_at=datetime.fromisoformat(row["started_at"]),
                    finished_at=datetime.fromisoformat(row["finished_at"]),
                    outcome=BudgetCommandStatus(str(row["outcome"])),
                    observed_before_minor=(
                        int(row["observed_before_minor"])
                        if row["observed_before_minor"] is not None
                        else None
                    ),
                    confirmed_after_minor=(
                        int(row["confirmed_after_minor"])
                        if row["confirmed_after_minor"] is not None
                        else None
                    ),
                    provider_trace_id=(
                        str(row["provider_trace_id"]) if row["provider_trace_id"] else None
                    ),
                    error=error,
                    reconciliation=bool(row["reconciliation"]),
                    actor_user_id=(UUID(row["actor_user_id"]) if row["actor_user_id"] else None),
                    credential_id=(UUID(row["credential_id"]) if row["credential_id"] else None),
                    authorization_snapshot=authorization_snapshot_from_payload(
                        self._snapshot_payload(row["authorization_snapshot_json"])
                    ),
                    credential_snapshot=credential_snapshot_from_payload(
                        self._snapshot_payload(row["credential_snapshot_json"])
                    ),
                )
            )
        return tuple(attempts)

    def _to_command(self, conn: sqlite3.Connection, row):
        from app.services.provider_budget_commands import (
            authorization_snapshot_from_payload,
            BudgetChangeRequest,
            BudgetCommandStatus,
            credential_snapshot_from_payload,
            ProviderBudgetCommand,
        )

        request = BudgetChangeRequest(
            client_id=UUID(row["client_id"]),
            agency_id=UUID(row["agency_id"]) if row["agency_id"] else None,
            ad_account_id=UUID(row["ad_account_id"]),
            provider_account_id=str(row["provider_account_id"]),
            credential_id=UUID(row["credential_id"]),
            target_type=str(row["target_type"]),
            provider_target_id=str(row["provider_target_id"]),
            field=str(row["budget_field"]),
            amount_minor=int(row["amount_minor"]),
            currency=str(row["currency"]),
            expected_current_minor=(
                int(row["expected_current_minor"])
                if row["expected_current_minor"] is not None
                else None
            ),
            reason=str(row["reason"]),
            source_action_id=UUID(row["source_action_id"]) if row["source_action_id"] else None,
        )
        command_id = UUID(row["id"])
        return ProviderBudgetCommand(
            id=command_id,
            request=request,
            actor_user_id=UUID(row["actor_user_id"]),
            authorization_snapshot=authorization_snapshot_from_payload(
                self._snapshot_payload(row["authorization_snapshot_json"])
            ),
            credential_snapshot=credential_snapshot_from_payload(
                self._snapshot_payload(row["credential_snapshot_json"])
            ),
            status=BudgetCommandStatus(str(row["status"])),
            idempotency_key=str(row["idempotency_key"]),
            request_hash=str(row["request_hash"]),
            preview_hash=str(row["preview_hash"]),
            observed_before_minor=int(row["observed_before_minor"]),
            confirmed_after_minor=(
                int(row["confirmed_after_minor"])
                if row["confirmed_after_minor"] is not None
                else None
            ),
            provider_trace_id=str(row["provider_trace_id"]) if row["provider_trace_id"] else None,
            error=self._error_from_row(row),
            attempt_count=int(row["attempt_count"]),
            attempts=self._attempts(conn, command_id),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=self._dt(row["completed_at"]),
        )

    def create_or_replay(
        self,
        *,
        request,
        actor_user_id: UUID,
        authorization_snapshot,
        credential_snapshot,
        idempotency_key: str,
        request_hash: str,
        preview_hash: str,
        observed_before_minor: int,
        now: datetime,
    ):
        from app.services.provider_budget_commands import (
            CommandSubmission,
            IdempotencyConflictError,
            validate_idempotency_key,
        )

        normalized_key = validate_idempotency_key(idempotency_key)
        created_at = self._iso(now)
        command_id = uuid4()
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM provider_budget_commands WHERE client_id=? AND idempotency_key=?",
                (str(request.client_id), normalized_key),
            ).fetchone()
            if existing:
                if not hmac.compare_digest(str(existing["request_hash"]), str(request_hash)):
                    raise IdempotencyConflictError(
                        "idempotency_conflict",
                        "Idempotency-Key was already used with a different request",
                    )
                command = self._to_command(conn, existing)
                conn.commit()
                return CommandSubmission(command=command, replayed=True)
            consumed_preview = conn.execute(
                "SELECT id FROM provider_budget_commands WHERE preview_hash=?",
                (preview_hash,),
            ).fetchone()
            if consumed_preview:
                raise IdempotencyConflictError(
                    "preview_already_consumed",
                    "Preview token was already consumed by another budget command",
                )
            quarantined = conn.execute(
                """
                SELECT command_id FROM provider_budget_target_quarantines
                WHERE provider='meta' AND target_type=? AND provider_target_id=? AND budget_field=?
                """,
                (request.target_type.value, request.provider_target_id, request.field.value),
            ).fetchone()
            if quarantined:
                from app.services.provider_budget_commands import CommandNotExecutableError

                raise CommandNotExecutableError(
                    "meta_budget_target_quarantined",
                    "An earlier budget write has an unresolved outcome; reconcile it before another change",
                )
            conn.execute(
                """
                INSERT INTO provider_budget_commands (
                  id, provider, client_id, agency_id, ad_account_id,
                  provider_account_id, credential_id, actor_user_id,
                  authorization_snapshot_json, credential_snapshot_json,
                  target_type, provider_target_id, budget_field, amount_minor,
                  currency, expected_current_minor, reason, source_action_id,
                  status, idempotency_key, request_hash, preview_hash,
                  observed_before_minor, confirmed_after_minor, provider_trace_id,
                  error_code, error_message, error_subcode, error_retryable,
                  attempt_count, created_at, updated_at, completed_at
                ) VALUES (
                  ?, 'meta', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  'queued', ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, 0, ?, ?, NULL
                )
                """,
                (
                    str(command_id),
                    str(request.client_id),
                    str(request.agency_id) if request.agency_id else None,
                    str(request.ad_account_id),
                    request.provider_account_id,
                    str(request.credential_id),
                    str(actor_user_id),
                    self._snapshot_json(authorization_snapshot),
                    self._snapshot_json(credential_snapshot),
                    request.target_type.value,
                    request.provider_target_id,
                    request.field.value,
                    request.amount_minor,
                    request.currency,
                    request.expected_current_minor,
                    request.reason,
                    str(request.source_action_id) if request.source_action_id else None,
                    normalized_key,
                    request_hash,
                    preview_hash,
                    observed_before_minor,
                    created_at,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            command = self._to_command(conn, row)
            conn.commit()
        return CommandSubmission(command=command, replayed=False)

    def get(self, command_id: UUID):
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            return self._to_command(conn, row) if row else None

    def target_quarantine_command_id(self, request) -> Optional[UUID]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT command_id FROM provider_budget_target_quarantines
                WHERE provider='meta' AND target_type=? AND provider_target_id=? AND budget_field=?
                """,
                (request.target_type.value, request.provider_target_id, request.field.value),
            ).fetchone()
        return UUID(row["command_id"]) if row else None

    def list(
        self,
        *,
        client_id: UUID,
        ad_account_id: Optional[UUID] = None,
        agency_id: Optional[UUID] = None,
        agency_id_is_null: bool = False,
        limit: int = 50,
    ):
        where = ["client_id=?"]
        params: List[object] = [str(client_id)]
        if ad_account_id is not None:
            where.append("ad_account_id=?")
            params.append(str(ad_account_id))
        if agency_id is not None:
            where.append("agency_id=?")
            params.append(str(agency_id))
        elif agency_id_is_null:
            where.append("agency_id IS NULL")
        params.append(max(1, min(int(limit), 200)))
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM provider_budget_commands WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._to_command(conn, row) for row in rows]

    def claim_queued(self, command_id: UUID, *, now: datetime):
        from app.services.provider_budget_commands import CommandNotExecutableError

        updated_at = self._iso(now)
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE provider_budget_commands
                SET status='in_progress', attempt_count=attempt_count+1, updated_at=?
                WHERE id=? AND status='queued'
                """,
                (updated_at, str(command_id)),
            )
            if cursor.rowcount != 1:
                row = conn.execute("SELECT status FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
                if not row:
                    raise CommandNotExecutableError("command_not_found", "Budget command was not found")
                raise CommandNotExecutableError(
                    "command_not_executable",
                    f"Budget command in {row['status']} state cannot execute again",
                )
            row = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            command = self._to_command(conn, row)
            conn.commit()
            return command

    @staticmethod
    def _error_values(error):
        return (
            error.code if error else None,
            error.message if error else None,
            error.subcode if error else None,
            1 if error and error.retryable else 0,
        )

    def _insert_attempt(self, conn: sqlite3.Connection, command_id: UUID, attempt) -> None:
        error_code, error_message, error_subcode, error_retryable = self._error_values(attempt.error)
        command_snapshots = conn.execute(
            "SELECT authorization_snapshot_json, credential_snapshot_json "
            "FROM provider_budget_commands WHERE id=?",
            (str(command_id),),
        ).fetchone()
        authorization_snapshot_json = (
            self._snapshot_json(attempt.authorization_snapshot)
            if attempt.authorization_snapshot is not None
            else str(command_snapshots["authorization_snapshot_json"] or "{}")
        )
        credential_snapshot_json = (
            self._snapshot_json(attempt.credential_snapshot)
            if attempt.credential_snapshot is not None
            else str(command_snapshots["credential_snapshot_json"] or "{}")
        )
        conn.execute(
            """
            INSERT INTO provider_budget_command_attempts (
              command_id, attempt_no, started_at, finished_at, outcome,
              observed_before_minor, confirmed_after_minor, provider_trace_id,
              error_code, error_message, error_subcode, error_retryable, reconciliation,
              actor_user_id, credential_id, authorization_snapshot_json, credential_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(command_id),
                attempt.attempt_no,
                self._iso(attempt.started_at),
                self._iso(attempt.finished_at),
                attempt.outcome.value,
                attempt.observed_before_minor,
                attempt.confirmed_after_minor,
                attempt.provider_trace_id,
                error_code,
                error_message,
                error_subcode,
                error_retryable,
                1 if attempt.reconciliation else 0,
                str(attempt.actor_user_id) if attempt.actor_user_id else None,
                str(attempt.credential_id) if attempt.credential_id else None,
                authorization_snapshot_json,
                credential_snapshot_json,
            ),
        )

    def finish_execution(
        self,
        command_id: UUID,
        *,
        status,
        attempt,
        confirmed_after_minor: Optional[int],
        provider_trace_id: Optional[str],
        error,
        now: datetime,
    ):
        from app.services.provider_budget_commands import (
            BudgetCommandStatus,
            BudgetValidationError,
            CommandNotExecutableError,
        )

        allowed = {
            BudgetCommandStatus.APPLIED,
            BudgetCommandStatus.CONFLICT,
            BudgetCommandStatus.FAILED,
            BudgetCommandStatus.UNKNOWN,
        }
        if status not in allowed:
            raise BudgetValidationError("invalid_transition", "Execution must finish in a terminal or unknown state")
        now_iso = self._iso(now)
        completed_at = None if status == BudgetCommandStatus.UNKNOWN else now_iso
        error_code, error_message, error_subcode, error_retryable = self._error_values(error)
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            if not row or row["status"] != "in_progress":
                raise CommandNotExecutableError("command_not_in_progress", "Budget command is not in progress")
            self._insert_attempt(conn, command_id, attempt)
            conn.execute(
                """
                UPDATE provider_budget_commands
                SET status=?, confirmed_after_minor=?, provider_trace_id=?,
                    error_code=?, error_message=?, error_subcode=?, error_retryable=?,
                    updated_at=?, completed_at=?
                WHERE id=? AND status='in_progress'
                """,
                (
                    status.value,
                    confirmed_after_minor,
                    provider_trace_id,
                    error_code,
                    error_message,
                    error_subcode,
                    error_retryable,
                    now_iso,
                    completed_at,
                    str(command_id),
                ),
            )
            if status == BudgetCommandStatus.UNKNOWN:
                self._upsert_target_quarantine(conn, row, now_iso=now_iso)
            updated = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            command = self._to_command(conn, updated)
            conn.commit()
            return command

    def record_reconciliation(
        self,
        command_id: UUID,
        *,
        status,
        attempt,
        confirmed_after_minor: Optional[int],
        provider_trace_id: Optional[str],
        error,
        now: datetime,
    ):
        from app.services.provider_budget_commands import (
            BudgetCommandStatus,
            BudgetValidationError,
            CommandNotExecutableError,
        )

        allowed = {BudgetCommandStatus.APPLIED, BudgetCommandStatus.CONFLICT, BudgetCommandStatus.UNKNOWN}
        if status not in allowed:
            raise BudgetValidationError("invalid_reconciliation", "Reconciliation cannot perform or retry a write")
        now_iso = self._iso(now)
        completed_at = None if status == BudgetCommandStatus.UNKNOWN else now_iso
        error_code, error_message, error_subcode, error_retryable = self._error_values(error)
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            if not row or row["status"] != "unknown":
                raise CommandNotExecutableError(
                    "command_not_reconcilable",
                    "Only an unknown command can be reconciled",
                )
            self._insert_attempt(conn, command_id, attempt)
            conn.execute(
                """
                UPDATE provider_budget_commands
                SET status=?, confirmed_after_minor=?, provider_trace_id=COALESCE(?, provider_trace_id),
                    error_code=?, error_message=?, error_subcode=?, error_retryable=?,
                    attempt_count=attempt_count+1, updated_at=?, completed_at=?
                WHERE id=? AND status='unknown'
                """,
                (
                    status.value,
                    confirmed_after_minor,
                    provider_trace_id,
                    error_code,
                    error_message,
                    error_subcode,
                    error_retryable,
                    now_iso,
                    completed_at,
                    str(command_id),
                ),
            )
            if status == BudgetCommandStatus.UNKNOWN:
                self._upsert_target_quarantine(conn, row, now_iso=now_iso)
            else:
                conn.execute(
                    """
                    DELETE FROM provider_budget_target_quarantines
                    WHERE provider='meta' AND target_type=? AND provider_target_id=?
                      AND budget_field=? AND command_id=?
                    """,
                    (
                        row["target_type"],
                        row["provider_target_id"],
                        row["budget_field"],
                        row["id"],
                    ),
                )
            updated = conn.execute("SELECT * FROM provider_budget_commands WHERE id=?", (str(command_id),)).fetchone()
            command = self._to_command(conn, updated)
            conn.commit()
            return command

    def acquire_target_lock(
        self,
        command,
        *,
        allow_quarantined_command: bool = False,
    ) -> Optional[str]:
        """Acquire a cross-process lease for one provider target/field."""
        now = datetime.now(timezone.utc)
        # The exact-target Meta saga is bounded to fewer than ten requests. A
        # fixed ten-minute lease remains above that worst-case request budget.
        # Expiry never authorizes takeover while a command is in progress.
        lease_until = now.timestamp() + 600
        lease_token = uuid4().hex
        request = command.request
        key = ("meta", request.target_type.value, request.provider_target_id, request.field.value)
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            quarantine = conn.execute(
                """
                SELECT command_id FROM provider_budget_target_quarantines
                WHERE provider=? AND target_type=? AND provider_target_id=? AND budget_field=?
                """,
                key,
            ).fetchone()
            if quarantine and not (
                allow_quarantined_command and str(quarantine["command_id"]) == str(command.id)
            ):
                conn.rollback()
                return None
            # An interrupted provider write remains a permanent runtime fence,
            # even if its lease row is absent or expired. Time cannot prove
            # whether Meta accepted a POST. Only the offline recovery command,
            # run after every API instance is stopped, may turn it into UNKNOWN.
            in_progress = conn.execute(
                """
                SELECT id FROM provider_budget_commands
                WHERE provider=? AND target_type=? AND provider_target_id=?
                  AND budget_field=? AND status='in_progress'
                LIMIT 1
                """,
                key,
            ).fetchone()
            if in_progress:
                conn.rollback()
                return None
            row = conn.execute(
                """
                SELECT l.command_id, l.lease_until, c.status AS command_status
                FROM provider_budget_target_locks l
                JOIN provider_budget_commands c ON c.id=l.command_id
                WHERE l.provider=? AND l.target_type=? AND l.provider_target_id=? AND l.budget_field=?
                """,
                key,
            ).fetchone()
            if row and datetime.fromisoformat(row["lease_until"]).timestamp() > now.timestamp():
                conn.rollback()
                return None
            if row and row["command_status"] == "in_progress":
                # Defensive duplicate of the target-wide check above for old
                # databases and unexpected rows: never steal from a write with
                # an unresolved provider outcome.
                conn.rollback()
                return None
            values = (
                *key,
                str(command.id),
                lease_token,
                datetime.fromtimestamp(lease_until, tz=timezone.utc).isoformat(),
                now.isoformat(),
            )
            conn.execute(
                """
                INSERT INTO provider_budget_target_locks (
                  provider, target_type, provider_target_id, budget_field,
                  command_id, lease_token, lease_until, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, target_type, provider_target_id, budget_field)
                DO UPDATE SET command_id=excluded.command_id, lease_token=excluded.lease_token,
                              lease_until=excluded.lease_until, updated_at=excluded.updated_at
                """,
                values,
            )
            conn.commit()
        return lease_token

    def release_target_lock(self, command, lease_token: str) -> None:
        request = command.request
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                DELETE FROM provider_budget_target_locks
                WHERE provider='meta' AND target_type=? AND provider_target_id=?
                  AND budget_field=? AND command_id=? AND lease_token=?
                  AND NOT EXISTS (
                    SELECT 1 FROM provider_budget_commands c
                    WHERE c.id=provider_budget_target_locks.command_id
                      AND c.status='in_progress'
                  )
                """,
                (
                    request.target_type.value,
                    request.provider_target_id,
                    request.field.value,
                    str(command.id),
                    lease_token,
                ),
            )
            conn.commit()

    def renew_target_lock(self, command, lease_token: str) -> bool:
        """Fence the provider POST by renewing only the exact live lease owner."""
        now = datetime.now(timezone.utc)
        request = command.request
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE provider_budget_target_locks
                SET lease_until=?, updated_at=?
                WHERE provider='meta' AND target_type=? AND provider_target_id=?
                  AND budget_field=? AND command_id=? AND lease_token=?
                  AND EXISTS (
                    SELECT 1 FROM provider_budget_commands c
                    WHERE c.id=provider_budget_target_locks.command_id
                      AND c.status='in_progress'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM provider_budget_target_quarantines q
                    WHERE q.provider='meta'
                      AND q.target_type=provider_budget_target_locks.target_type
                      AND q.provider_target_id=provider_budget_target_locks.provider_target_id
                      AND q.budget_field=provider_budget_target_locks.budget_field
                  )
                """,
                (
                    datetime.fromtimestamp(now.timestamp() + 600, tz=timezone.utc).isoformat(),
                    now.isoformat(),
                    request.target_type.value,
                    request.provider_target_id,
                    request.field.value,
                    str(command.id),
                    lease_token,
                ),
            )
            if int(cursor.rowcount or 0) == 1:
                touched = conn.execute(
                    """
                    UPDATE provider_budget_commands
                    SET updated_at=?
                    WHERE id=? AND status='in_progress'
                    """,
                    (now.isoformat(), str(command.id)),
                )
                if int(touched.rowcount or 0) != 1:
                    conn.rollback()
                    return False
            conn.commit()
        return int(cursor.rowcount or 0) == 1

    def count_interrupted_in_progress(self) -> int:
        """Count unresolved writes without changing runtime state."""
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM provider_budget_commands WHERE status='in_progress'"
            ).fetchone()
        return int(row["count"] or 0)

    def recover_interrupted_in_progress_offline(
        self,
        *,
        all_api_instances_stopped: bool,
        now: Optional[datetime] = None,
    ) -> int:
        """Quarantine interrupted writes during an explicitly offline repair.

        This method must never be called from request handling, constructors or
        application startup. A blue/green candidate can share the same SQLite
        database with the active API, so only an operator who has stopped every
        API instance can prove that an old worker cannot resume and POST later.
        """
        if all_api_instances_stopped is not True:
            raise ValueError("all API instances must be stopped before provider budget recovery")
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        checked_at = checked_at.astimezone(timezone.utc)
        recovered = 0
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM provider_budget_commands
                WHERE status='in_progress'
                ORDER BY updated_at ASC
                """
            ).fetchall()
            for row in rows:
                previous_attempt = conn.execute(
                    "SELECT MAX(attempt_no) AS attempt_no FROM provider_budget_command_attempts WHERE command_id=?",
                    (row["id"],),
                ).fetchone()
                attempt_no = max(
                    1,
                    int(row["attempt_count"] or 0),
                    int(previous_attempt["attempt_no"] or 0) + 1,
                )
                conn.execute(
                    """
                    INSERT INTO provider_budget_command_attempts (
                      command_id, attempt_no, started_at, finished_at, outcome,
                      observed_before_minor, confirmed_after_minor, provider_trace_id,
                      error_code, error_message, error_subcode, error_retryable, reconciliation,
                      actor_user_id, credential_id, authorization_snapshot_json, credential_snapshot_json
                    ) VALUES (?, ?, ?, ?, 'unknown', NULL, NULL, NULL, ?, ?, NULL, 0, 0, NULL, NULL, ?, ?)
                    """,
                    (
                        row["id"],
                        attempt_no,
                        row["updated_at"],
                        checked_at.isoformat(),
                        "provider_execution_interrupted",
                        "Provider write outcome is unknown after an interrupted execution; reconcile by reading only.",
                        row["authorization_snapshot_json"],
                        row["credential_snapshot_json"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE provider_budget_commands
                    SET status='unknown', attempt_count=?,
                        error_code='provider_execution_interrupted',
                        error_message=?, error_subcode=NULL, error_retryable=0,
                        updated_at=?, completed_at=NULL
                    WHERE id=? AND status='in_progress'
                    """,
                    (
                        attempt_no,
                        "Provider write outcome is unknown after an interrupted execution; reconcile by reading only.",
                        checked_at.isoformat(),
                        row["id"],
                    ),
                )
                self._upsert_target_quarantine(conn, row, now_iso=checked_at.isoformat())
                conn.execute(
                    "DELETE FROM provider_budget_target_locks WHERE command_id=?",
                    (row["id"],),
                )
                recovered += 1
            conn.commit()
        return recovered
