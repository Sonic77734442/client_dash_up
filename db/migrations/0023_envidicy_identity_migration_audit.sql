-- Additive operator checkpoints only: never seed identities, users or grants.
CREATE TABLE public.envidicy_identity_migration_audit (
  run_id uuid NOT NULL,
  row_number integer NOT NULL CHECK (row_number > 0),
  manifest_sha256 text NOT NULL CHECK (length(manifest_sha256) = 64),
  row_sha256 text NOT NULL CHECK (length(row_sha256) = 64),
  user_id uuid NOT NULL REFERENCES public.users(id),
  issuer text NOT NULL,
  subject text NOT NULL,
  operator_ref text NOT NULL,
  provenance_ref text NOT NULL,
  validation_ref text NOT NULL,
  action text NOT NULL CHECK (action IN ('linked', 'confirmed')),
  created_at timestamptz NOT NULL,
  PRIMARY KEY (run_id, row_number),
  UNIQUE (run_id, user_id),
  UNIQUE (run_id, issuer, subject)
);
