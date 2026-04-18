-- PostgreSQL hard enforcement for active overlap prevention by scope.
CREATE EXTENSION IF NOT EXISTS btree_gist;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'budgets_active_client_overlap_excl'
  ) THEN
    ALTER TABLE public.budgets
      ADD CONSTRAINT budgets_active_client_overlap_excl
      EXCLUDE USING gist (
        client_id WITH =,
        daterange(start_date, end_date, '[]') WITH &&
      )
      WHERE (status = 'active' AND scope = 'client');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'budgets_active_account_overlap_excl'
  ) THEN
    ALTER TABLE public.budgets
      ADD CONSTRAINT budgets_active_account_overlap_excl
      EXCLUDE USING gist (
        account_id WITH =,
        daterange(start_date, end_date, '[]') WITH &&
      )
      WHERE (status = 'active' AND scope = 'account');
  END IF;
END $$;
