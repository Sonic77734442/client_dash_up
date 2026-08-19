# Integration credential encryption rollout

Integration credential payloads are encrypted with AES-256-GCM before they are
written to SQLite. The authenticated context includes the credential row id,
provider, scope, and connection key, so copying ciphertext between tenants or
rows fails integrity verification.

## Configuration

Configure both deployment secrets before connecting or reconnecting a provider:

```text
INTEGRATION_CREDENTIAL_ENCRYPTION_KEYS={"2026-08-primary":"<base64url-encoded 32-byte key>"}
INTEGRATION_CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID=2026-08-primary
```

Key ids are non-secret rotation labels. Key material must remain only in the
deployment secret manager. If both variables are absent, the application may
boot and read legacy plaintext rows for existing report synchronization, but it
refuses every new persistent credential write. A partial or malformed keyring
fails configuration validation.

## Existing-row migration

Back up the database first. The one-off command is a dry-run unless `--apply` is
present and prints aggregate counts only:

```text
python scripts/rotate_integration_credentials.py --db-path <existing-sqlite-db>
python scripts/rotate_integration_credentials.py --db-path <existing-sqlite-db> --apply
```

The apply phase authenticates/decrypts every candidate before writing any row,
then performs compare-and-swap updates in one transaction. A corrupt row,
missing old key, or concurrent update aborts safely. Rotation does not change a
credential's semantic `updated_at`, so it cannot reorder tenant credential
resolution.

Do not apply the rotation in the same rollout that first introduces encrypted
envelopes. Deploy the encryption-capable build with provider money writes still
disabled, verify normal read synchronization, and keep a recoverable database
backup. Apply the rotation only after that build is stable. Rolling back to a
build that predates encrypted-envelope support requires restoring the pre-
rotation database backup; the old build cannot read encrypted rows.

For a normal key rotation, add the new key while retaining the old key, change
the active key id, restart, dry-run, apply, verify `needs_rotation` is zero, and
only then remove the old key.

## Provider money-write readiness

Legacy plaintext credentials are never write-eligible. Encryption alone is not
enough: Meta writes additionally require current validated app/config metadata,
`ads_management`, and an explicit row in
`provider_account_credential_bindings` created after successful hardened
discovery/reconnect.

Historical `ad_accounts.metadata.integration_credential_id` values are not
trusted or backfilled. Reconnecting, replacing, moving, or archiving a
credential removes its bindings and requires verified discovery again.
