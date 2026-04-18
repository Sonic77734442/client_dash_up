CREATE TABLE IF NOT EXISTS public.users (
  id uuid PRIMARY KEY,
  email text NULL UNIQUE,
  name text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin', 'agency', 'client')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.auth_identities (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.users(id),
  provider text NOT NULL,
  provider_user_id text NOT NULL,
  email text NULL,
  email_verified boolean NULL,
  raw_profile jsonb NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(provider, provider_user_id)
);

CREATE TABLE IF NOT EXISTS public.user_client_access (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.users(id),
  client_id uuid NOT NULL REFERENCES public.clients(id),
  role text NOT NULL CHECK (role IN ('agency', 'client')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, client_id)
);

CREATE TABLE IF NOT EXISTS public.sessions (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.users(id),
  token_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz NULL,
  metadata jsonb NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.auth_provider_configs (
  id uuid PRIMARY KEY,
  provider text NOT NULL UNIQUE,
  client_id text NOT NULL,
  client_secret text NOT NULL,
  redirect_uri text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_identities_user_id ON public.auth_identities(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_identities_provider ON public.auth_identities(provider);
CREATE INDEX IF NOT EXISTS idx_user_client_access_user_id ON public.user_client_access(user_id);
CREATE INDEX IF NOT EXISTS idx_user_client_access_client_id ON public.user_client_access(client_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON public.sessions(user_id);
