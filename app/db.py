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
    conn.execute("DROP INDEX IF EXISTS uq_integration_credentials_scope_provider")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_scope_provider_connection "
        "ON integration_credentials(provider, scope_type, scope_id, connection_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_integration_credentials_status "
        "ON integration_credentials(status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_integration_credentials_scope "
        "ON integration_credentials(scope_type, scope_id, status)"
    )


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
