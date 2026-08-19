# Unified Facebook login and Meta Ads rollout

This runbook keeps ordinary Facebook sign-in, Meta Ads synchronization, and
future budget writes inside one Meta App without merging application users,
roles, tenants, or sessions.

Production identifiers used by the current plan:

- primary Meta App: `3177364462435934`;
- legacy Facebook-login App: `4427635210887765`;
- canonical callback:
  `https://dash.envidicy.kz/api/backend/auth/facebook/callback`;
- existing Business Login Configuration: `1560512595854726` (leave unchanged
  until the replacement configuration is tested).

Never paste App Secrets, access tokens, encryption keys, session cookies, or
preview-signing secrets into tickets, source control, screenshots, or videos.

## Phase 0 — prepare and keep writes off

1. Back up the persistent SQLite database and verify that the backup can be
   restored.
2. Keep these flags disabled:
   `META_BUDGET_COMMANDS_ENABLED=false`,
   `META_BUDGET_ADMIN_WRITES_ENABLED=false`, and
   `META_BUDGET_SPEND_CAP_ENABLED=false`.
3. Create a new Facebook Login for Business configuration for the primary app.
   Request only the permissions the product demonstrates. The initial Dash
   rollout needs `ads_management`; do not add `business_management` unless
   Business Portfolio enumeration is actually enabled and demonstrated.
4. Keep the existing Business Login configuration active until the replacement
   completes a full test connection, discovery, and read synchronization.

## Phase 1 — provision secrets before deploying the new build

In the Render secret manager, set the complete encryption keyring and active key
id, plus a separate random preview-signing secret. Keep all budget flags off.

Configure ordinary login and Business Login separately even when they use the
same primary Meta App:

- `FACEBOOK_AUTH_*` points to the primary app ordinary-login credentials;
- `FACEBOOK_CLIENT_ID`, `FACEBOOK_CLIENT_SECRET`, and `FACEBOOK_REDIRECT_URI`
  point to the primary app Business Login credentials, while
  `FACEBOOK_LOGIN_CONFIG_ID` initially remains on the currently deployed
  configuration;
- all three `FACEBOOK_AUTH_LEGACY_*` values point to the old login app during the
  migration window. A partial legacy configuration intentionally blocks login.

Both redirect URI variables must use the canonical Dash callback above. Do not
start OAuth from the Vercel alias because host-only nonce/session cookies cannot
cross to `dash.envidicy.kz`.

## Phase 2 — deploy safely

1. Deploy the encryption-capable build with provider budget controls disabled.
2. Files `0019` and `0020` are PostgreSQL migrations and must be applied only
   to the PostgreSQL database where that backend is used. SQLite is upgraded
   idempotently by application startup; take and verify a SQLite backup before
   starting the new build, then confirm the expected tables/columns afterward.
3. Verify health, password login, Google login, existing report reads, and an
   existing Meta read synchronization before rotating any stored credentials.
4. Rolling back to a build without encrypted-envelope support is safe without
   restoring the database only when no credential was created, reconnected, or
   otherwise written after this deployment. If any credential write occurred
   (including before bulk rotation), stay on an encryption-capable build or
   restore the verified pre-deployment database backup; the older build cannot
   read encrypted envelopes.

## Phase 3 — migrate Facebook identities

1. An already-linked primary Facebook identity signs in normally.
2. A legacy user who receives `facebook_migration_required` chooses
   **Transfer Facebook sign-in**. The user proves the legacy app identity and
   then the primary app identity in two single-use OAuth steps.
3. The migration links the new app-scoped identity to the same internal user. It
   never merges by email, reassigns another identity, creates another workspace,
   or changes the user's platform/agency/client role.
4. Keep the legacy configuration present until the admin identity inventory
   confirms that every required legacy user also has a primary-app identity.
   Only then remove all three legacy variables together.

## Phase 4 — rotate and rebind Meta connections

1. Run the credential rotation command in dry-run mode and review aggregate
   counts only.
2. Apply the rotation atomically. Verify that no active credential remains
   plaintext or requires an unavailable key.
3. Only after the existing connection still synchronizes, switch
   `FACEBOOK_LOGIN_CONFIG_ID` to the tested replacement configuration and put
   the previous id in `FACEBOOK_LOGIN_LEGACY_CONFIG_IDS`. Existing verified
   credentials may then continue read synchronization during the migration
   window, but they are never accepted for provider budget writes.
4. Reconnect Meta using the replacement Business Login configuration.
5. Run provider discovery again for each test account. Discovery—not mutable
   account metadata—creates the trusted account-to-credential binding.
6. Run a 30-day Meta sync and verify both the job result and actual data
   freshness. A valid zero-row response is shown as **no activity**, not as a
   transport failure.
7. Remove the previous id from `FACEBOOK_LOGIN_LEGACY_CONFIG_IDS` only after all
   required tenant connections have been reconnected and rediscovered.

## Phase 5 — controlled budget pilot

1. Enable `META_BUDGET_COMMANDS_ENABLED` only for the controlled production
   rollout after `ads_management`, encrypted credential storage, and trusted
   discovery binding are all ready.
2. Leave account-level `spend_cap` disabled. The first public scope is limited to
   existing campaign/ad-set `daily_budget` or `lifetime_budget` owners.
3. Use an agency owner/manager or the exact solo owner. Client viewers and agency
   members remain read-only; platform-admin writes require their separate flag
   and a platform-owned global credential.
4. Start with a small test campaign. Verify the live value, preview, mandatory
   reason, explicit confirmation, provider read-back, and durable history.
5. If a provider response is ambiguous, the command becomes `unknown`. Do not
   submit another write. Use read-only reconciliation until the result is known.

## Meta App Review video

Record one continuous English-captioned flow on `dash.envidicy.kz`: application
login, agency/client selection, Facebook Login for Business consent and asset
selection, Meta-specific account discovery, successful read synchronization,
dashboard metrics, budget preview, explicit confirmation, and verified read-back
on a safe test campaign. State clearly that `ads_management` is used for the
demonstrated budget update and that other campaign-management operations are not
yet exposed. Do not show Render, developer secrets, tokens, cookies, real client
names, personal data, or unrelated WhatsApp/Page permissions.
