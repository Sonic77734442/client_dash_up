-- Integration credential payloads written after this migration are stored as
-- versioned AES-GCM JSON envelopes by the application. Existing plaintext JSON
-- remains readable for report sync during the explicit application-level
-- rotation, but is never eligible for provider money writes.
--
-- Do not derive/backfill bindings from ad_accounts.metadata. A row is created
-- only after a successful post-hardening provider discovery/reconnect.

BEGIN;

ALTER TABLE public.integration_credentials
  ADD COLUMN IF NOT EXISTS connection_key text NOT NULL DEFAULT 'default';

ALTER TABLE public.integration_credentials
  ADD COLUMN IF NOT EXISTS semantic_revision integer NOT NULL DEFAULT 1;

ALTER TABLE public.integration_credentials
  DROP CONSTRAINT IF EXISTS integration_credentials_semantic_revision_check;

ALTER TABLE public.integration_credentials
  ADD CONSTRAINT integration_credentials_semantic_revision_check
  CHECK (semantic_revision > 0);

DROP INDEX IF EXISTS public.uq_integration_credentials_scope_provider;
DROP INDEX IF EXISTS public.uq_integration_credentials_scope_provider_connection;

-- PostgreSQL also treats NULL values as distinct in a conventional unique
-- index. Refuse to choose between existing live grants; the transaction rolls
-- back without deleting or archiving credentials and the operator must resolve
-- the duplicate identity explicitly.
DO $$
DECLARE
  duplicate_groups integer;
BEGIN
  SELECT count(*) INTO duplicate_groups
  FROM (
    SELECT provider, scope_type, scope_id, connection_key
    FROM public.integration_credentials
    WHERE (scope_type = 'global' AND scope_id IS NULL)
       OR (scope_type IN ('agency', 'client') AND scope_id IS NOT NULL)
    GROUP BY provider, scope_type, scope_id, connection_key
    HAVING count(*) > 1
  ) duplicates;

  IF duplicate_groups > 0 THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P0001',
      MESSAGE = 'integration_credential_scope_duplicates',
      DETAIL = duplicate_groups || ' duplicate credential scope identity group(s); no credential was modified';
  END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_global_connection
  ON public.integration_credentials(provider, connection_key)
  WHERE scope_type = 'global' AND scope_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_scoped_connection
  ON public.integration_credentials(provider, scope_type, scope_id, connection_key)
  WHERE scope_type IN ('agency', 'client') AND scope_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.provider_account_credential_bindings (
  ad_account_id uuid NOT NULL REFERENCES public.ad_accounts(id) ON DELETE CASCADE,
  client_id uuid NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  provider text NOT NULL,
  external_account_id text NOT NULL,
  credential_id uuid NOT NULL REFERENCES public.integration_credentials(id) ON DELETE RESTRICT,
  bound_by uuid NULL REFERENCES public.users(id) ON DELETE SET NULL,
  bound_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ad_account_id, provider),
  CHECK (length(trim(provider)) > 0),
  CHECK (length(trim(external_account_id)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_provider_account_credential_bindings_credential
  ON public.provider_account_credential_bindings(credential_id);

CREATE INDEX IF NOT EXISTS idx_provider_account_credential_bindings_identity
  ON public.provider_account_credential_bindings(provider, external_account_id);

COMMENT ON TABLE public.provider_account_credential_bindings IS
  'Server-owned, explicit provider account to credential bindings; never backfilled from mutable metadata.';

COMMENT ON COLUMN public.integration_credentials.credentials_json IS
  'Application-encrypted versioned credential envelope; legacy plaintext is read-only and must be rotated.';

COMMENT ON COLUMN public.integration_credentials.semantic_revision IS
  'Non-secret semantic version incremented for reconnects and credential edits; encryption-only rotation does not change it.';

COMMIT;
