ALTER TABLE public.ad_account_sync_jobs
  ADD COLUMN IF NOT EXISTS error_code text NULL,
  ADD COLUMN IF NOT EXISTS error_category text NULL,
  ADD COLUMN IF NOT EXISTS retryable boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS attempt int NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS next_retry_at timestamptz NULL;

CREATE INDEX IF NOT EXISTS idx_ad_account_sync_jobs_next_retry_at
  ON public.ad_account_sync_jobs(next_retry_at);
