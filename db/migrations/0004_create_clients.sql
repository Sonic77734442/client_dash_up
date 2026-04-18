CREATE TABLE IF NOT EXISTS public.clients (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  legal_name text NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'archived')),
  default_currency text NOT NULL DEFAULT 'USD',
  timezone text NULL,
  notes text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clients_status ON public.clients(status);
