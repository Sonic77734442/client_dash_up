# Auth / Roles / Tenant Model (Architecture-Only)

Stage: local architecture validation. This document defines target model only.

## Tenant model
- Primary tenant boundary: `client_id`.
- `ad_accounts`, `ad_stats`, budgets (account scope) are tenant-owned via `client_id` relationship.
- Agency-level users can access multiple tenants; client-level users can access only assigned tenants.

## Roles (proposed)
- `owner`: full access to assigned tenants, including budgets and account configuration.
- `manager`: operational write access to accounts/stats/budgets in assigned tenants.
- `analyst`: read access + ad-stats ingestion for assigned tenants.
- `viewer`: read-only access for assigned tenants.
- `agency_admin`: full cross-tenant access.

## Authorization rules (target)
- Every request resolves actor identity and tenant grants.
- Any `client_id`/`account_id` in request must be validated against actor grants.
- Cross-tenant access denied by default.
- Agency overview endpoint restricted to `agency_admin` (or equivalent elevated role).
- `user_client_access` semantics:
  - unique pair (`user_id`, `client_id`)
  - row defines tenant assignment and assignment role (`agency` or `client`)
  - repeated assignment updates role, does not create duplicate grants

## Security assumptions (explicit)
- External authentication does **not** grant internal authorization automatically.
- Tenant isolation must be enforced by backend authorization checks on every tenant-scoped request.
- A provider identity (`provider + provider_user_id`) must map to exactly one internal user.

## Local validation scope
- Full auth is intentionally deferred.
- API contract and service boundaries are designed so auth middleware can be inserted later without route redesign.
- Platform admin MVP (implemented as internal plumbing):
  - Agency entity (`agencies`)
  - Agency members (`agency_members`)
  - Agency tenant bindings (`agency_client_access`)
  - Binding a client to an agency materializes `user_client_access` for active agency members.
