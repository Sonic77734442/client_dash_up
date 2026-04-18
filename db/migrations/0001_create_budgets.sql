CREATE TABLE IF NOT EXISTS public.budgets (
  id uuid PRIMARY KEY,
  client_id uuid NOT NULL,
  account_id uuid NULL,
  amount numeric(14,2) NOT NULL,
  currency text NOT NULL DEFAULT 'USD',
  period_type text NOT NULL CHECK (period_type IN ('monthly', 'custom')),
  start_date date NOT NULL,
  end_date date NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  version int NOT NULL DEFAULT 1,
  note text NULL,
  created_by uuid NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_budgets_client_id ON public.budgets(client_id);
CREATE INDEX IF NOT EXISTS idx_budgets_account_id ON public.budgets(account_id);
CREATE INDEX IF NOT EXISTS idx_budgets_client_period ON public.budgets(client_id, start_date, end_date);
