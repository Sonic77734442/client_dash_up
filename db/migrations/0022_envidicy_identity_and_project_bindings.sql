-- Additive only. No legacy identities, users, memberships or business data move.
CREATE TABLE public.envidicy_id_principals (
  user_id uuid PRIMARY KEY REFERENCES public.users(id),
  issuer text NOT NULL,
  subject text NOT NULL,
  created_at timestamptz NOT NULL,
  UNIQUE(issuer, subject)
);

CREATE TABLE public.envidicy_project_bindings (
  project_id uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  client_id uuid NOT NULL UNIQUE REFERENCES public.clients(id),
  status text NOT NULL CHECK (status IN ('active','inactive')),
  operator_ref text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE public.envidicy_login_transactions (
  state_hash text PRIMARY KEY,
  browser_hash text NOT NULL,
  nonce text NOT NULL,
  code_verifier text NOT NULL,
  next_path text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('login','logout')),
  expires_at timestamptz NOT NULL
);
CREATE INDEX idx_envidicy_login_expiry ON public.envidicy_login_transactions(expires_at);
