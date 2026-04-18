CREATE TABLE IF NOT EXISTS public.oauth_states (
  state text PRIMARY KEY,
  provider text NOT NULL,
  next_path text NULL,
  expires_at timestamptz NOT NULL,
  used_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_provider ON public.oauth_states(provider, created_at);
