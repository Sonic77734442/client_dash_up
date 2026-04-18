CREATE TABLE IF NOT EXISTS public.ad_stats (
  id uuid PRIMARY KEY,
  ad_account_id uuid NOT NULL REFERENCES public.ad_accounts(id),
  date date NOT NULL,
  platform text NOT NULL,
  impressions bigint NOT NULL DEFAULT 0,
  clicks bigint NOT NULL DEFAULT 0,
  spend numeric(14,2) NOT NULL,
  conversions numeric(14,2) NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(ad_account_id, date, platform)
);

CREATE INDEX IF NOT EXISTS idx_ad_stats_date ON public.ad_stats(date);
CREATE INDEX IF NOT EXISTS idx_ad_stats_platform ON public.ad_stats(platform);
CREATE INDEX IF NOT EXISTS idx_ad_stats_account_date ON public.ad_stats(ad_account_id, date);
