CREATE TABLE IF NOT EXISTS public.agency_invites (
  id uuid PRIMARY KEY,
  agency_id uuid NOT NULL REFERENCES public.agencies(id),
  email text NOT NULL,
  member_role text NOT NULL DEFAULT 'member' CHECK (member_role IN ('owner','manager','member')),
  token_hash text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','revoked','expired')),
  expires_at timestamptz NOT NULL,
  invited_by uuid NULL REFERENCES public.users(id),
  accepted_user_id uuid NULL REFERENCES public.users(id),
  accepted_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agency_invites_agency_id ON public.agency_invites(agency_id);
CREATE INDEX IF NOT EXISTS idx_agency_invites_email ON public.agency_invites(email);
CREATE INDEX IF NOT EXISTS idx_agency_invites_status ON public.agency_invites(status);
