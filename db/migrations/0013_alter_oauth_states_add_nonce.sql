ALTER TABLE public.oauth_states
ADD COLUMN IF NOT EXISTS nonce text NOT NULL DEFAULT '';
