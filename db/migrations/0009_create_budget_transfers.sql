CREATE TABLE IF NOT EXISTS public.budget_transfers (
  id BIGSERIAL PRIMARY KEY,
  source_budget_id UUID NOT NULL REFERENCES public.budgets(id) ON DELETE CASCADE,
  target_budget_id UUID NOT NULL REFERENCES public.budgets(id) ON DELETE CASCADE,
  amount NUMERIC(14,2) NOT NULL,
  note TEXT NULL,
  changed_by UUID NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_budget_transfers_source ON public.budget_transfers(source_budget_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_budget_transfers_target ON public.budget_transfers(target_budget_id, created_at DESC);
