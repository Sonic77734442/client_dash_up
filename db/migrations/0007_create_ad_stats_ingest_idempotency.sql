CREATE TABLE IF NOT EXISTS public.ad_stats_ingest_idempotency (
  idempotency_key text PRIMARY KEY,
  request_hash text NOT NULL,
  response_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
