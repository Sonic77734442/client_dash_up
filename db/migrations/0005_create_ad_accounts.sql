CREATE TABLE IF NOT EXISTS public.ad_accounts (
  id uuid PRIMARY KEY,
  client_id uuid NOT NULL REFERENCES public.clients(id),
  platform text NOT NULL,
  external_account_id text NOT NULL,
  name text NOT NULL,
  currency text NOT NULL DEFAULT 'USD',
  timezone text NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'archived')),
  metadata jsonb NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(platform, external_account_id)
);

CREATE INDEX IF NOT EXISTS idx_ad_accounts_client_id ON public.ad_accounts(client_id);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_status ON public.ad_accounts(status);
CREATE INDEX IF NOT EXISTS idx_ad_accounts_platform ON public.ad_accounts(platform);
