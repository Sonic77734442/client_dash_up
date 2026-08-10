-- Adds the explicit single-tenant owner role. Tenant membership remains in
-- user_client_access; application ACLs require exactly one active client.
BEGIN;

ALTER TABLE public.users
  DROP CONSTRAINT IF EXISTS users_role_check;

ALTER TABLE public.users
  ADD CONSTRAINT users_role_check
  CHECK (role IN ('admin', 'agency', 'client', 'solo_client'));

COMMIT;
