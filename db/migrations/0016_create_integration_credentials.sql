CREATE TABLE IF NOT EXISTS public.integration_credentials (
  id uuid PRIMARY KEY,
  provider text NOT NULL,
  scope_type text NOT NULL CHECK (scope_type IN ('global','agency','client')),
  scope_id uuid NULL,
  credentials_json jsonb NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_by uuid NULL REFERENCES public.users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((scope_type='global' AND scope_id IS NULL) OR (scope_type IN ('agency','client') AND scope_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_credentials_scope_provider
  ON public.integration_credentials(provider, scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_integration_credentials_status
  ON public.integration_credentials(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_integration_credentials_scope
  ON public.integration_credentials(scope_type, scope_id, status);
