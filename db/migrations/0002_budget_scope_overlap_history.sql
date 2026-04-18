ALTER TABLE public.budgets
  ADD COLUMN IF NOT EXISTS scope text;

UPDATE public.budgets
SET scope = CASE WHEN account_id IS NULL THEN 'client' ELSE 'account' END
WHERE scope IS NULL;

ALTER TABLE public.budgets
  ALTER COLUMN scope SET DEFAULT 'client';

ALTER TABLE public.budgets
  ALTER COLUMN scope SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'budgets_scope_check'
  ) THEN
    ALTER TABLE public.budgets
      ADD CONSTRAINT budgets_scope_check
      CHECK (scope IN ('client', 'account'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'budgets_scope_account_consistency_check'
  ) THEN
    ALTER TABLE public.budgets
      ADD CONSTRAINT budgets_scope_account_consistency_check
      CHECK (
        (scope = 'client' AND account_id IS NULL)
        OR
        (scope = 'account' AND account_id IS NOT NULL)
      );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_budgets_scope ON public.budgets(scope);

CREATE TABLE IF NOT EXISTS public.budget_history (
  id bigserial PRIMARY KEY,
  budget_id uuid NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now(),
  changed_by uuid NULL,
  previous_values jsonb NOT NULL,
  new_values jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_budget_history_budget_id ON public.budget_history(budget_id, changed_at DESC);
