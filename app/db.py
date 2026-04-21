import sqlite3
from pathlib import Path


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

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_accounts_platform_external_id ON ad_accounts(platform, external_account_id);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_client_id ON ad_accounts(client_id);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_status ON ad_accounts(status);

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
  role TEXT NOT NULL CHECK (role IN ('admin','agency','client')),
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
  credentials_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_by TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((scope_type='global' AND scope_id IS NULL) OR (scope_type IN ('agency','client') AND scope_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_scope_provider
ON integration_credentials(provider, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_integration_credentials_status ON integration_credentials(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_credentials_scope ON integration_credentials(scope_type, scope_id, status);

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
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_states_provider ON oauth_states(provider, created_at);

CREATE TABLE IF NOT EXISTS budgets (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'client' CHECK (scope IN ('client','account')),
  account_id TEXT NULL,
  amount NUMERIC NOT NULL,
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
  amount NUMERIC NOT NULL,
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
"""


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if user_cols and "password_hash" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NULL")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(budgets)").fetchall()}
    if cols and "scope" not in cols:
        conn.execute("ALTER TABLE budgets ADD COLUMN scope TEXT NOT NULL DEFAULT 'client'")
        conn.execute("UPDATE budgets SET scope=CASE WHEN account_id IS NULL THEN 'client' ELSE 'account' END")

    oauth_cols = {row[1] for row in conn.execute("PRAGMA table_info(oauth_states)").fetchall()}
    if oauth_cols and "nonce" not in oauth_cols:
        conn.execute("ALTER TABLE oauth_states ADD COLUMN nonce TEXT NOT NULL DEFAULT ''")

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

    cred_cols = {row[1] for row in conn.execute("PRAGMA table_info(integration_credentials)").fetchall()}
    if cred_cols and "status" not in cred_cols:
        conn.execute("ALTER TABLE integration_credentials ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if cred_cols and "created_by" not in cred_cols:
        conn.execute("ALTER TABLE integration_credentials ADD COLUMN created_by TEXT NULL")


def init_sqlite(db_path: str) -> None:
    path = Path(db_path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(DDL)
        _migrate_sqlite(conn)
        conn.commit()


def sqlite_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
