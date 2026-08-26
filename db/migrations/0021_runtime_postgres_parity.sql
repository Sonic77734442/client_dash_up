-- Runtime parity for the SQLite-backed application stores. This migration is
-- intentionally additive except for replacing the obsolete global ad-account
-- identity constraint with the current per-client identity rule.

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS password_hash text NULL;

ALTER TABLE public.agencies
  ADD COLUMN IF NOT EXISTS allow_client_invites boolean NOT NULL DEFAULT true;

ALTER TABLE public.oauth_states
  ADD COLUMN IF NOT EXISTS initiator_user_id uuid NULL;

CREATE TABLE IF NOT EXISTS public.ad_account_sync_leases (
  lease_key text PRIMARY KEY,
  lease_token text NOT NULL,
  lease_until timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ad_account_sync_leases_until
  ON public.ad_account_sync_leases(lease_until);

CREATE TABLE IF NOT EXISTS public.client_invites (
  id uuid PRIMARY KEY,
  client_id uuid NOT NULL REFERENCES public.clients(id),
  email text NOT NULL,
  token_hash text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','accepted','revoked','expired')),
  expires_at timestamptz NOT NULL,
  invited_by uuid NULL REFERENCES public.users(id),
  accepted_user_id uuid NULL REFERENCES public.users(id),
  accepted_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_invites_client_id
  ON public.client_invites(client_id);
CREATE INDEX IF NOT EXISTS idx_client_invites_email
  ON public.client_invites(email);
CREATE INDEX IF NOT EXISTS idx_client_invites_status
  ON public.client_invites(status);

CREATE TABLE IF NOT EXISTS public.operational_actions (
  id uuid PRIMARY KEY,
  action text NOT NULL CHECK (action IN ('scale','cap','pause','review')),
  scope text NOT NULL CHECK (scope IN ('account','client','agency')),
  scope_id text NOT NULL,
  title text NOT NULL,
  reason text NOT NULL,
  metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
  client_id uuid NULL,
  account_id uuid NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','applied','failed')),
  created_by uuid NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_operational_actions_created_at
  ON public.operational_actions(created_at);
CREATE INDEX IF NOT EXISTS idx_operational_actions_client_id
  ON public.operational_actions(client_id);
CREATE INDEX IF NOT EXISTS idx_operational_actions_account_id
  ON public.operational_actions(account_id);

CREATE TABLE IF NOT EXISTS public.audit_logs (
  id bigserial PRIMARY KEY,
  event_type text NOT NULL,
  resource_type text NOT NULL,
  resource_id text NULL,
  actor_user_id uuid NULL,
  actor_role text NULL,
  tenant_client_id uuid NULL,
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
  ON public.audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type
  ON public.audit_logs(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user_id
  ON public.audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_client_id
  ON public.audit_logs(tenant_client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.alerts (
  id uuid PRIMARY KEY,
  code text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('critical','high','medium','low')),
  status text NOT NULL CHECK (status IN ('open','acked','resolved')),
  title text NOT NULL,
  message text NOT NULL,
  fingerprint text NOT NULL UNIQUE,
  provider text NULL,
  client_id uuid NULL,
  ad_account_id uuid NULL,
  context_json jsonb NOT NULL CHECK (jsonb_typeof(context_json) = 'object'),
  occurrences integer NOT NULL DEFAULT 1 CHECK (occurrences > 0),
  first_seen_at timestamptz NOT NULL,
  last_seen_at timestamptz NOT NULL,
  acknowledged_at timestamptz NULL,
  acknowledged_by uuid NULL,
  resolved_at timestamptz NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_status_severity
  ON public.alerts(status, severity, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_provider_client
  ON public.alerts(provider, client_id, last_seen_at DESC);

ALTER TABLE public.ad_accounts
  DROP CONSTRAINT IF EXISTS ad_accounts_platform_external_account_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_accounts_client_platform_external_id
  ON public.ad_accounts(client_id, platform, external_account_id);

CREATE INDEX IF NOT EXISTS idx_ad_accounts_assignment
  ON public.ad_accounts(platform, external_account_id, status);

CREATE OR REPLACE FUNCTION public.canonical_ad_account_platform(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
  SELECT CASE WHEN lower(trim(value)) IN ('meta', 'facebook') THEN 'meta' ELSE lower(trim(value)) END
$$;

CREATE OR REPLACE FUNCTION public.canonical_ad_account_external_id(platform_value text, external_value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
  SELECT CASE
    WHEN public.canonical_ad_account_platform(platform_value) = 'meta' THEN
      CASE
        WHEN lower(trim(external_value)) LIKE 'act\_%' ESCAPE '\'
          THEN trim(substring(trim(external_value) FROM 5))
        ELSE trim(external_value)
      END
    WHEN public.canonical_ad_account_platform(platform_value) IN ('google', 'tiktok') THEN
      COALESCE(NULLIF(regexp_replace(trim(external_value), '[^0-9]', '', 'g'), ''), trim(external_value))
    ELSE trim(external_value)
  END
$$;

CREATE OR REPLACE FUNCTION public.guard_active_ad_account_assignment()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.status <> 'active' THEN
    RETURN NEW;
  END IF;

  -- The lock closes the read/check/insert race even when callers do not begin
  -- an explicit serialized transaction before writing an ad account.
  PERFORM pg_advisory_xact_lock(7318042611214);

  IF EXISTS (
    SELECT 1
    FROM public.ad_accounts account
    JOIN public.clients owner ON owner.id = account.client_id AND owner.status = 'active'
    WHERE account.id <> NEW.id
      AND account.client_id <> NEW.client_id
      AND account.status = 'active'
      AND public.canonical_ad_account_platform(account.platform)
          = public.canonical_ad_account_platform(NEW.platform)
      AND public.canonical_ad_account_external_id(account.platform, account.external_account_id)
          = public.canonical_ad_account_external_id(NEW.platform, NEW.external_account_id)
  ) THEN
    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'assignment_conflict';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_ad_accounts_active_assignment_insert ON public.ad_accounts;
CREATE TRIGGER trg_ad_accounts_active_assignment_insert
BEFORE INSERT ON public.ad_accounts
FOR EACH ROW EXECUTE FUNCTION public.guard_active_ad_account_assignment();

DROP TRIGGER IF EXISTS trg_ad_accounts_active_assignment_update ON public.ad_accounts;
CREATE TRIGGER trg_ad_accounts_active_assignment_update
BEFORE UPDATE OF client_id, platform, external_account_id, status ON public.ad_accounts
FOR EACH ROW EXECUTE FUNCTION public.guard_active_ad_account_assignment();

CREATE OR REPLACE FUNCTION public.guard_client_assignment_restore()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.status <> 'active' OR OLD.status = 'active' THEN
    RETURN NEW;
  END IF;

  PERFORM pg_advisory_xact_lock(7318042611214);
  IF EXISTS (
    SELECT 1
    FROM public.ad_accounts own
    JOIN public.ad_accounts other
      ON other.client_id <> NEW.id AND other.status = 'active'
    JOIN public.clients other_client
      ON other_client.id = other.client_id AND other_client.status = 'active'
    WHERE own.client_id = NEW.id
      AND own.status = 'active'
      AND public.canonical_ad_account_platform(own.platform)
          = public.canonical_ad_account_platform(other.platform)
      AND public.canonical_ad_account_external_id(own.platform, own.external_account_id)
          = public.canonical_ad_account_external_id(other.platform, other.external_account_id)
  ) THEN
    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'assignment_conflict';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_clients_active_assignment_restore ON public.clients;
CREATE TRIGGER trg_clients_active_assignment_restore
BEFORE UPDATE OF status ON public.clients
FOR EACH ROW EXECUTE FUNCTION public.guard_client_assignment_restore();

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'budgets_amount_nonnegative_check'
      AND conrelid = 'public.budgets'::regclass
  ) THEN
    ALTER TABLE public.budgets
      ADD CONSTRAINT budgets_amount_nonnegative_check CHECK (amount >= 0) NOT VALID;
    ALTER TABLE public.budgets VALIDATE CONSTRAINT budgets_amount_nonnegative_check;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'budget_transfers_amount_positive_check'
      AND conrelid = 'public.budget_transfers'::regclass
  ) THEN
    ALTER TABLE public.budget_transfers
      ADD CONSTRAINT budget_transfers_amount_positive_check CHECK (amount > 0) NOT VALID;
    ALTER TABLE public.budget_transfers VALIDATE CONSTRAINT budget_transfers_amount_positive_check;
  END IF;
END
$$;
