# Dash ID / My consumer contract v1

This document describes the implemented Dash consumer interface. It is not a
provider approval, a deployment record, or authorization to publish or activate
the integration. Examples contain field names only, not operational identifiers
or credentials.

## Status and boundaries

- ID login and My authority consumption are implemented behind default-off
  switches. Implementation, local tests, deployment and production acceptance
  are separate states.
- Confirmation of the deployed My provider contract is pending. The authority
  schema below is the schema Dash currently validates, not a claim that a
  particular My deployment already supplies it.
- The Connect consumer contract is pending. Existing direct provider adapters
  do not constitute a completed Connect integration.
- Preservation of all legacy admin, agency and solo-client capabilities through
  My roles is pending. The current ID scope is a single project/client scope;
  it is not a general role migration.
- Deploy compatible preparation and test only with an explicitly admitted test
  cohort before a separately reviewed real-user migration. Keep legacy login
  enabled during that phase. A server-enforced cohort boundary is a pilot
  precondition; hiding a login button or limiting My grants is insufficient.

## Addresses and browser entry

| Interface | Exact value |
| --- | --- |
| ID issuer | `https://id.envidicy.com/realms/envidicy` |
| OIDC client ID | `envidicy-dash` |
| Dash origin | `https://dash.envidicy.kz` |
| Public launch | `GET /auth/envidicy` on the Dash origin |
| Same-origin backend relay | `/api/backend` on the Dash origin |
| OIDC callback | `https://dash.envidicy.kz/api/backend/auth/envidicy/callback` |
| Logout callback | `https://dash.envidicy.kz/api/backend/auth/envidicy/logout/callback` |
| My authority endpoint | `POST https://my.envidicy.com/api/v1/authority/resolve` |
| My navigation | `https://my.envidicy.com/products` |

The public launch returns a 303 to the same-origin backend start route. It
accepts only a validated local `next` return path, defaulting to `/portal`.
Organization, project, permission and session-token query parameters never
grant access. The issuer, client ID and production callback origin are fixed
by the Dash implementation, not selected by the browser.

The browser uses the same-origin relay, not the upstream API origin. The routes
below are backend paths; browser callers prefix them with `/api/backend`.

| Method and path | Behavior |
| --- | --- |
| `GET /auth/envidicy/config` | Non-secret login policy; `Cache-Control: no-store`. |
| `GET /auth/envidicy/start` | Creates a browser-bound OIDC transaction, then redirects to ID. |
| `GET /auth/envidicy/callback` | Consumes the transaction once, validates ID exchange, issues a Dash session and checks My before choosing the browser destination. |
| `POST /auth/envidicy/logout` | Revokes the selected Dash session and returns `logout_url`; cookie authentication requires CSRF protection. |
| `GET /auth/envidicy/logout/callback` | Consumes the logout transaction and redirects to `/login`. |
| `GET /auth/me` | Existing user/session response with additive ID authority context. |
| `POST /auth/logout` | Existing local Dash logout remains available. |

The configuration response has these fields:

```json
{
  "enabled": false,
  "auto_login": false,
  "local_auth_enabled": true,
  "login_url": "/api/backend/auth/envidicy/start",
  "my_url": "https://my.envidicy.com/products"
}
```

`enabled` describes configured ID entry, not My health, a user's entitlement,
project mapping, or full release readiness. `local_auth_enabled` is independent
of ID health. On policy network errors, 5xx or malformed responses, the UI shows
a retry state rather than promising a password fallback. Compatibility with an
older backend is limited to the existing policy response without the additive
field, or an absent configuration route returning 404.

`auto_login` selects automatic browser entry, not permission to access data.
It is true only when ID is configured and either
`ENVIDICY_ID_AUTO_LOGIN_ENABLED=true` or ID-only mode is enabled. The automatic
entry switch defaults off and does not close legacy authentication. A missing
`auto_login` field on an older backend keeps manual entry; malformed policy does
not trigger authentication or restore local login.

For an unauthenticated browser, opening Dash preserves the requested local path,
query and fragment and starts the existing ID authorization flow after both
session and policy checks complete. Existing valid sessions are not restarted.
Login and registration belong to ID; Dash does not create a separate password
registration flow or grant access to newly registered identities.

Any callback error, an explicit logout, or an automatic attempt that returns
without a valid session stops automatic re-entry. A per-tab marker contains no
tokens or identity data; unavailable browser storage falls back to an explicit
ID link. Manual retry remains possible. During the dual-login pilot only,
`/login?legacy=1` explicitly selects the still-enabled legacy form. This query
cannot override the server's ID-only policy.

## Identity and sessions

- OIDC uses Authorization Code, PKCE S256, `client_secret_basic`, state and nonce.
  The transaction is browser-bound, single-use and expires after five minutes.
  ID tokens require the configured issuer, audience, signature, allowed
  algorithm, nonce and valid timestamps. OIDC tokens are not returned to the
  browser as Dash sessions or persisted as provider credentials.
- Identity is the exact verified `(issuer, subject)` pair. Email and display
  name cannot merge accounts, select a tenant or assign a role.
- An admitted new identity can have a separate local `client` projection without
  email, password or persistent tenant grants. This is not migration of a legacy
  account. Pilot admission must happen before creating that projection or
  issuing a session, including for direct callback access.
- Existing users require explicit reviewed binding to their existing user IDs.
  Binding conflicts must not merge, replace or delete an existing projection.
  Project binding separately maps an exact My organization/project to an
  existing active Dash client; it never creates a replacement business workspace.
- Session provenance is stored per session. Adding an identity binding does not
  turn a previously issued password/social session into an ID session.
- A Dash ID session lasts at most **15 minutes from original issuance**, with no
  generic refresh extension. Continuing requires another ID authorization flow.
  My authority is re-evaluated on protected requests; local Dash user disable is
  checked during session validation.
- ID-side disable/logout is not pushed into Dash by a backchannel callback.
  The accepted maximum remaining Dash session window is 15 minutes, unless My
  or Dash denies access sooner. Logout does not promise to end other products'
  sessions. Selected-token precedence is Bearer, then X-Session-Token, then cookie.

## My authority request and validation

Dash calls the authority endpoint server-to-server with a dedicated caller
Bearer credential. It sends `Accept: application/json`, `Cache-Control: no-store`
and a fresh request identifier in both `X-Request-ID` and the JSON body:

| Request field | Value/type |
| --- | --- |
| `contract_version` | `envidicy.authority.v1` |
| `request_id` | Fresh UUID string |
| `product` | `dash.analytics` |
| `principal` | Object with exactly `iss` and `sub` for the verified identity |

No browser-supplied organization or project is forwarded. My owns the selected
context. Dash requires HTTP 200 JSON and validates the following response shape;
unknown/duplicate fields, identity/request mismatch and invalid lifetimes fail
closed. Redirects are not followed.

| Response branch | Required fields |
| --- | --- |
| Both | `contract_version`, `request_id`, `decision`, `principal`, `evaluated_at` |
| `decision: deny` | Additionally `reason_code`; no allow fields |
| `decision: allow` | Additionally `membership`, `entitlements`, `permissions`, `valid_until`, `authority_revision`, `revocation` |

The contract version, request ID and principal must match the request exactly.
All authority timestamps have explicit timezones. `evaluated_at` may be at most
five minutes old or 30 seconds ahead. Allow validity must be in the future and
no later than 120 seconds after the earlier of evaluation and the current time.
Dash does not persist this result as a legacy grant or use an expired allow on
upstream failure.

An allow response contains these exact nested fields:

- `membership`: `status`, `kind`, `organization_id`, `organization_name`,
  `project_id`, `project_name`, `brand_id`, `brand_name`, `timezone`, `roles`,
  `version`, `valid_from`, `valid_until`. Active direct membership and canonical
  organization/project UUIDs are required. Names/timezone and the role list must
  satisfy the consumer's bounded-string/list validation.
- `entitlements`: exactly one object with `code`, `status`, `version`,
  `valid_from`, `valid_until`; its code is `dash.analytics` and its interval is
  active. Overall allow validity cannot exceed membership or entitlement validity.
- `permissions`: a duplicate-free subset of `dash.analytics`,
  `dash.analytics.read`, `dash.analytics.manage`. Base and `.read` are both
  required for data access.
- `revocation`: `membership_generation`, `entitlement_generation`, `checked_at`.
  Generations are positive safe integers matching the corresponding versions;
  `checked_at` equals `evaluated_at`.
- `authority_revision`: a nonempty bounded string identifying the authority
  revision. It is not interpreted as a grant in its own right.

## Session authority and permitted operations

`GET /auth/me` retains the `{user, session}` envelope. For a valid ID session,
the response projects `user.role` and `session.role` as `client`,
`global_access: false`, `access_scope: assigned`, and adds:

| Session field | Meaning |
| --- | --- |
| `auth_source` | `envidicy_id` |
| `authority.product` | `dash.analytics` |
| `authority.my_url` | Fixed My navigation URL |
| `authority.access_state` | `ready`, `not_granted`, `project_unlinked`, or `context_unavailable` |
| `authority.redirect_to_my` | True only for a fully validated My `decision:deny`; not for an inconsistent allow, pilot exclusion, missing local binding or upstream failure |
| `authority.organization_id`, `project_id` | Canonical context IDs when available; otherwise null |
| `authority.permissions` | Validated permissions; empty when access is not ready |
| `accessible_client_ids` | Only the active mapped client when ready; otherwise empty |

Validated allow metadata can also include organization/project names, expiry and
authority revision. Internal session-provenance/issuance fields are not serialized.
Legacy responses omit the additive ID authority fields.

A valid ID session may receive `/auth/me` HTTP 200 even when My access is not
ready, allowing a stable explanation and My navigation instead of a login loop.
Protected data routes return `envidicy_access_required`: HTTP 403 for denied or
unlinked access, HTTP 503 for unavailable authority. Missing or invalid Dash
authentication remains an authentication error, not a My entitlement denial.

The callback redirects confirmed My denials directly to the fixed My navigation
URL. The frontend applies the same trusted decision to an existing ID session,
including when access is revoked. Neither redirects to a URL supplied by a user
or an authority payload, nor appends the Dash return path to My. A ready, mapped
session returns to the validated Dash destination. Missing local project binding
remains a setup state; unavailable or malformed authority remains a retry state.
Those states preserve the authenticated ID session without granting data access
or repeatedly sending the browser through ID. Every protected data request still
checks current authority independently of the callback's routing decision.

An otherwise valid allow snapshot without the required base and read permissions
does not establish a My denial: Dash rejects access locally and does not hand the
browser to My. The provider must issue an explicit deny for unavailable access.

Base plus `.read` enables scoped reads. Additional `.manage` permits only local
planned-budget operations through existing `/budgets`, `/budgets/{id}` and
`/budgets/{id}/transfer` routes, still within the mapped client. It does not grant
agency/global-admin privileges, user management, credential changes, account
provisioning, sync execution, paid operations or provider campaign/budget writes.
Unsupported writes return HTTP 403 `envidicy_operation_not_available`.

## Deployment states and configuration

| `ENVIDICY_ID_ENABLED` | `ENVIDICY_ID_ONLY_ENABLED` | State |
| --- | --- | --- |
| false | false | Compatible preparation; legacy login stays open. |
| true | false | Dual-login pilot, only after server-side test-cohort admission is verified. |
| true | true | Later ID-only cutover, only after migration and role/access acceptance. |
| false | true | ID unavailable and local login still closed; no implicit fallback. |

All login switches default off. Invalid ID-only or automatic-entry policy
configuration returns HTTP 503.
When ID-only is on, local human login/mint/refresh and old human sessions cannot
bypass it. Dedicated service authentication remains separate. No change to the
15-minute ID-session policy is required for these states.

The test-cohort configuration is `ENVIDICY_ID_PILOT_SUBJECTS`, a backend-only JSON
array of exact verified ID subject strings under the fixed issuer. An absent
variable preserves unrestricted identity admission; it must therefore be
explicitly configured before enabling a test-only pilot. Present but invalid,
blank or empty-array configuration closes ID admission. Subject identifiers are
not put into frontend configuration, source examples or public reports.

The pilot guard must check the verified subject after OIDC exchange but before
projection creation or session issuance, and recheck it when validating ID
access. Exclusion returns the safe `envidicy_pilot_only` login error, preserving
the validated return path and any existing legacy session. Admission never
replaces My authority or client-scope checks. These additions require their own
local regression and deployment verification before the pilot is activated.

Backend configuration includes production cookie/origin settings and two distinct
credentials: `ENVIDICY_ID_CLIENT_SECRET` or its `_FILE` alternative, and
`ENVIDICY_MY_AUTHORITY_TOKEN` or its `_FILE` alternative. Configure exactly one
source per credential. Never expose these as `NEXT_PUBLIC_*` variables.

Frontend configuration is `NEXT_PUBLIC_API_BASE=/api/backend`, a server-only
`API_UPSTREAM_BASE` for the selected backend deployment, and
`NEXT_PUBLIC_ENABLE_TOKEN_LOGIN=false`. No identity/authority secret belongs in
the frontend deployment.

The release requires the additive identity/binding and migration-audit schema,
including ordered migrations 0022 and 0023 for PostgreSQL. With PostgreSQL,
explicitly select `DATABASE_BACKEND=postgresql`, keep
`DATABASE_AUTO_MIGRATE=false` in web processes and use the existing migration
runner in pre-deploy. Applying schema is not a real-user import. A database-engine
cutover is a separate operation and should not be combined with this pilot.

Before activation: verify a restored backup, schema-compatible rollback, actual
backend/frontend revisions, same-origin cookies/callbacks, legacy login, strict
test admission, test-project mapping and provider-confirmed authority behavior.
Never recover by deleting migration ledger entries or restoring an old database
over newer writes. Successful isolated test-account access does not prove
real-user migration, legacy role preservation or Connect acceptance.

## Operator commands and migration contract

Run these tools only against the deliberately selected runtime database, using
its protected environment configuration. Shell variables below are placeholders
for independently reviewed values, not discovered or inferred account mappings.
Keep real manifests, identifiers and evidence outside source control and public
reports. Schema preparation, project binding and user migration are separate
operations; none enables ID login or grants My permissions automatically.

### Schema preparation

`python scripts/check_migrations.py` checks the ordered migration definitions;
it does not apply database changes. The PostgreSQL deployment/pre-deploy command
is `python scripts/migrate_postgres.py`, which applies pending migrations and
checks the migration ledger/checksums. Do not execute individual SQL files or
modify old migration entries. See [migration instructions](../../db/migrations/README.md).

Both binding tools below require an already initialized database, disable
automatic migration and validate the PostgreSQL ledger when PostgreSQL is
selected. They reject a missing SQLite database instead of creating one. Deploy
the additive schema and trusted per-session provenance before identity binding.

### Project binding

```bash
python scripts/envidicy_bind_project.py --organization-id "$ORG_ID" --project-id "$PROJECT_ID" --client-id "$CLIENT_ID" --operator-ref "$APPROVED_CHANGE_REF"
```

Without `--apply`, this checks canonical identifiers, the existing active Dash
client and binding conflicts. Verify the organization/project independently in
My: this command does not look them up remotely. Only after review, repeat the
same arguments with `--apply`. Results are `checked`, `applied` or `unchanged`;
an identical active mapping is idempotent. Conflicting, inactive or reassigned
mappings are rejected rather than overwritten. The command creates neither
tenants nor external grants.

### Existing-user identity binding

```bash
python scripts/envidicy_migrate_identities.py --manifest-file "$MANIFEST_FILE"
```

The exact JSON contract is `envidicy.dash.identity-migration.v1`:

- Top-level fields: `contract_version`, `run_id`, `operator_ref`, `rows`.
- Each row: `legacy_user_id`, `issuer`, `subject`, `provenance_ref`, `validation_ref`.
- `run_id` and `legacy_user_id` use canonical UUID strings; issuer/subject must
  identify the independently verified ID identity. References identify reviewed
  evidence but do not prove its authenticity by themselves.
- Extra or duplicate object fields, duplicate users/principals and invalid
  references are rejected. The manifest has 1–10,000 rows and is at most 1 MiB.
  Email, password, token, role and grant fields are not accepted.

The reader requires a canonical absolute path to a nonempty UTF-8 JSON file on
POSIX. It must be a regular file owned by the invoking effective user, mode
`0600`, with one hard link and no symlink path. File identity/metadata changes
during reading are rejected. Windows manifest-file execution is unsupported.

Dry-run is the default. Do not append `--apply` for real users until identity and
role mappings are approved, backup restoration and the complete procedure have
passed on an isolated restored copy, and concurrent changes are controlled.
The test-account pilot does not satisfy those migration gates. Applying adds
only explicit bindings and audit checkpoints to existing active users; it does
not create or merge users, alter their IDs/roles, import accounts into ID/My,
grant access or activate cutover.

Each row's binding and checkpoint commit atomically. Resume with the identical
manifest, run ID, row order and evidence references; completed rows remain
unchanged. A conflict stops further processing without erasing already committed
rows. Preserve the protected manifest and audit for review. Rollback requires a
schema-compatible build and reconciliation of newer writes, never deleted ledger
entries, automatic projection removal or an old database restored over live data.

## Local verification and acceptance boundaries

Existing local regression suites cover OIDC validation, transactions/replay,
explicit identity/project binding, trusted session origin, 15-minute expiry,
refresh rejection, My denial/outage/revocation, tenant boundaries, local-budget
permissions and default-off/ID-only login behavior:

- `tests/test_envidicy_oidc.py`
- `tests/test_envidicy_bridge.py`
- `tests/test_envidicy_routes.py`
- `tests/test_envidicy_cutover.py`
- `tests/test_envidicy_identity_migration.py`
- `frontend/tests/smoke/envidicy-entry-helper.spec.ts`
- `frontend/tests/smoke/envidicy-entry.spec.ts`

Run backend suites only with an isolated test database. Frontend checks are
`npm run build`, then `npm run typecheck`, then `npm run lint`; run Playwright
against the corresponding local build. Mocked ID/My browser tests verify UI
behavior, not live provider availability. PostgreSQL and OS-specific skipped
cases remain separate gates, not passes.

Pilot admission regressions must additionally prove that a non-cohort identity
cannot create a projection, obtain a session or replace an existing browser's
legacy session; denied attempts cannot poison later explicit mapping. Real-user
migration acceptance must prove reuse of original user/client IDs and data,
idempotent resume/conflict handling, preserved roles and scoped access on the
actual deployed revisions. These are acceptance requirements, not a statement
that production verification has already occurred.
