-- user_client_access.role is a materialization marker, not a per-client ACL.
-- Reconcile legacy rows with the active agency graph. Merely changing an
-- orphan row's marker to `agency` would leave its full write scope active and
-- make it impossible to revoke through the direct-access endpoint.
BEGIN;

DELETE FROM public.user_client_access AS access
USING public.users AS users
WHERE users.id = access.user_id
  AND (
    users.role = 'admin'
    OR (
      users.role = 'agency'
      AND (
        users.status <> 'active'
        OR NOT EXISTS (
          SELECT 1
          FROM public.agency_members AS member
          JOIN public.agencies AS agency ON agency.id = member.agency_id
          JOIN public.agency_client_access AS binding ON binding.agency_id = member.agency_id
          WHERE member.user_id = access.user_id
            AND binding.client_id = access.client_id
            AND member.status = 'active'
            AND agency.status = 'active'
        )
      )
    )
  );

UPDATE public.user_client_access AS access
SET role = CASE WHEN users.role = 'agency' THEN 'agency' ELSE 'client' END
FROM public.users AS users
WHERE users.id = access.user_id
  AND access.role <> CASE WHEN users.role = 'agency' THEN 'agency' ELSE 'client' END;

COMMIT;
