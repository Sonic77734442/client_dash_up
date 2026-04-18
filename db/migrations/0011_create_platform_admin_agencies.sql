CREATE TABLE IF NOT EXISTS public.agencies (
  id uuid PRIMARY KEY,
  name text NOT NULL UNIQUE,
  slug text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  plan text NOT NULL DEFAULT 'starter',
  notes text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.agency_members (
  id uuid PRIMARY KEY,
  agency_id uuid NOT NULL REFERENCES public.agencies(id),
  user_id uuid NOT NULL REFERENCES public.users(id),
  role text NOT NULL DEFAULT 'member' CHECK (role IN ('owner','manager','member')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(agency_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.agency_client_access (
  id uuid PRIMARY KEY,
  agency_id uuid NOT NULL REFERENCES public.agencies(id),
  client_id uuid NOT NULL REFERENCES public.clients(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(agency_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_agency_members_agency_id ON public.agency_members(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_members_user_id ON public.agency_members(user_id);
CREATE INDEX IF NOT EXISTS idx_agency_client_access_agency_id ON public.agency_client_access(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_client_access_client_id ON public.agency_client_access(client_id);
