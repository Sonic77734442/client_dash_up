# UI Role Matrix (Pre-MVP Freeze)

Date: 2026-04-16
Status: pre-enforcement architecture contract (frontend-first phase)

## 1) Product contours

- Agency Console (current primary contour)
  - Used by: `admin`, `agency`
  - Includes operational modules: dashboard, accounts, budgets, clients, sync diagnostics
- Client Portal (next contour, role-sliced)
  - Used by: `client`
  - Limited to assigned tenant data (`user_client_access`), no global operational controls

## 2) Role access baseline

- `admin`
  - Full visibility and actions across all clients/accounts
  - Can manage users, access grants, provider configs, sync runs, mappings, budgets
- `agency`
  - Visibility/actions only for assigned `client_id` scope
  - Can run sync and manage mappings/budgets only inside assigned tenants
- `client`
  - Read-first access to assigned client scope
  - No cross-tenant access, no global settings/admin operations

## 3) Screen visibility matrix

- `/` Dashboard
  - admin: full
  - agency: assigned tenants only
  - client: client-scoped variant (later split)
- `/accounts` Accounts Registry
  - admin: full
  - agency: assigned tenants only
  - client: hidden (or read-only subset in future portal)
- `/budgets` Budget Control
  - admin: full
  - agency: assigned tenants only
  - client: read-only subset in future portal
- `/clients` Client Operations
  - admin: full CRUD
  - agency: assigned-tenants CRUD subset (as policy allows)
  - client: hidden
- `/client/[id]` Client Details
  - admin: full
  - agency: assigned tenants only
  - client: own client only

## 4) Feature authority matrix (target behavior)

- Run sync (`POST /ad-accounts/sync/run`)
  - admin: yes
  - agency: yes (assigned tenants)
  - client: no
- Account mapping (assign account -> client)
  - admin: yes
  - agency: yes (assigned tenants)
  - client: no
- Budget create/patch/archive/transfer
  - admin: yes
  - agency: yes (assigned tenants)
  - client: no (for MVP), later optional approval workflow
- Auth/internal plumbing endpoints (`/auth/internal/*`)
  - internal/admin-only

## 5) Current implementation stage

- UI is in shared Agency Console form, with backend-aware data contracts.
- ACL full enforcement for all business routes is intentionally deferred until post-frontend MVP.
- Tenant filtering scaffolding and auth context exist; strict blocking rollout is next stage.

## 6) Next screens queue (agreed order)

1. Integrations Hub (implemented)
   - Provider connection health, permission/token hints, reconnect actions, sanitized events.
2. Sync Monitor (implemented)
   - Job history, per-provider/per-account statuses, retry controls.
3. Client Portal shell (implemented)
   - Slim read-only navigation + role-scoped contracts for client sessions.

## 7) Non-negotiable data contract rules

- Spend source of truth: ad APIs -> normalized `ad_stats`.
- Budget source of truth: internal manual `budgets` table only.
- Provider campaign budgets are never treated as client budget.
- Accounts synced from providers must be mapped under internal clients for tenant-safe rollups.
- `/portal` Client Portal
  - admin: optional read-only access (for QA), not primary workflow
  - agency: optional read-only access (for QA), not primary workflow
  - client: primary shell (read-only, assigned tenants only)
