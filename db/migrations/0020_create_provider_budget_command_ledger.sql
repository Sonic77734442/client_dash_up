BEGIN;

CREATE TABLE IF NOT EXISTS public.provider_budget_commands (
  id uuid PRIMARY KEY,
  provider text NOT NULL CHECK (provider IN ('meta')),
  client_id uuid NOT NULL REFERENCES public.clients(id),
  agency_id uuid NULL REFERENCES public.agencies(id),
  ad_account_id uuid NOT NULL REFERENCES public.ad_accounts(id),
  provider_account_id text NOT NULL,
  credential_id uuid NOT NULL REFERENCES public.integration_credentials(id),
  actor_user_id uuid NOT NULL REFERENCES public.users(id),
  authorization_snapshot_json jsonb NOT NULL CHECK (jsonb_typeof(authorization_snapshot_json)='object'),
  credential_snapshot_json jsonb NOT NULL CHECK (jsonb_typeof(credential_snapshot_json)='object'),
  target_type text NOT NULL CHECK (target_type IN ('campaign','ad_set','account')),
  provider_target_id text NOT NULL,
  budget_field text NOT NULL CHECK (budget_field IN ('daily_budget','lifetime_budget','spend_cap')),
  amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
  currency text NOT NULL,
  expected_current_minor bigint NULL CHECK (expected_current_minor IS NULL OR expected_current_minor >= 0),
  reason text NOT NULL,
  source_action_id uuid NULL,
  status text NOT NULL CHECK (status IN ('queued','in_progress','applied','conflict','failed','unknown')),
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,
  preview_hash text NOT NULL,
  observed_before_minor bigint NOT NULL CHECK (observed_before_minor >= 0),
  confirmed_after_minor bigint NULL CHECK (confirmed_after_minor IS NULL OR confirmed_after_minor >= 0),
  provider_trace_id text NULL,
  error_code text NULL,
  error_message text NULL,
  error_subcode text NULL,
  error_retryable boolean NOT NULL DEFAULT false,
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  completed_at timestamptz NULL,
  UNIQUE(client_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS public.provider_budget_command_attempts (
  id bigserial PRIMARY KEY,
  command_id uuid NOT NULL REFERENCES public.provider_budget_commands(id),
  attempt_no integer NOT NULL CHECK (attempt_no > 0),
  started_at timestamptz NOT NULL,
  finished_at timestamptz NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('applied','conflict','failed','unknown')),
  observed_before_minor bigint NULL CHECK (observed_before_minor IS NULL OR observed_before_minor >= 0),
  confirmed_after_minor bigint NULL CHECK (confirmed_after_minor IS NULL OR confirmed_after_minor >= 0),
  provider_trace_id text NULL,
  error_code text NULL,
  error_message text NULL,
  error_subcode text NULL,
  error_retryable boolean NOT NULL DEFAULT false,
  reconciliation boolean NOT NULL DEFAULT false,
  actor_user_id uuid NULL REFERENCES public.users(id),
  credential_id uuid NULL REFERENCES public.integration_credentials(id),
  authorization_snapshot_json jsonb NOT NULL CHECK (jsonb_typeof(authorization_snapshot_json)='object'),
  credential_snapshot_json jsonb NOT NULL CHECK (jsonb_typeof(credential_snapshot_json)='object'),
  UNIQUE(command_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS public.provider_budget_target_locks (
  provider text NOT NULL,
  target_type text NOT NULL,
  provider_target_id text NOT NULL,
  budget_field text NOT NULL,
  command_id uuid NOT NULL REFERENCES public.provider_budget_commands(id),
  lease_token text NOT NULL,
  lease_until timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (provider, target_type, provider_target_id, budget_field)
);

CREATE TABLE IF NOT EXISTS public.provider_budget_target_quarantines (
  provider text NOT NULL,
  target_type text NOT NULL,
  provider_target_id text NOT NULL,
  budget_field text NOT NULL,
  command_id uuid NOT NULL UNIQUE REFERENCES public.provider_budget_commands(id),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (provider, target_type, provider_target_id, budget_field)
);

CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_client_created
  ON public.provider_budget_commands(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_account_created
  ON public.provider_budget_commands(ad_account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_actor_created
  ON public.provider_budget_commands(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_budget_commands_status_updated
  ON public.provider_budget_commands(status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_budget_commands_preview_hash
  ON public.provider_budget_commands(preview_hash);
CREATE INDEX IF NOT EXISTS idx_provider_budget_attempts_command
  ON public.provider_budget_command_attempts(command_id, attempt_no);
CREATE INDEX IF NOT EXISTS idx_provider_budget_target_locks_expiry
  ON public.provider_budget_target_locks(lease_until);
CREATE INDEX IF NOT EXISTS idx_provider_budget_target_quarantines_command
  ON public.provider_budget_target_quarantines(command_id);

COMMIT;
