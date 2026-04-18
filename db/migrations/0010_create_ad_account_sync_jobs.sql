CREATE TABLE IF NOT EXISTS public.ad_account_sync_jobs (
  id uuid PRIMARY KEY,
  ad_account_id uuid NOT NULL REFERENCES public.ad_accounts(id),
  provider text NOT NULL,
  status text NOT NULL CHECK (status IN ('success', 'error')),
  started_at timestamptz NOT NULL,
  finished_at timestamptz NULL,
  records_synced bigint NOT NULL DEFAULT 0,
  error_message text NULL,
  request_meta jsonb NULL,
  created_by uuid NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_account_sync_jobs_account
  ON public.ad_account_sync_jobs(ad_account_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_ad_account_sync_jobs_status
  ON public.ad_account_sync_jobs(status, started_at DESC);
