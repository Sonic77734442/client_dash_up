# Frontend (Next.js)

## Run

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on `http://localhost:5173`.

## Smoke test

```bash
cd frontend
npm run test:smoke
```

Note: this assumes local frontend is running on `127.0.0.1:5173`.

## Backend connection

Set API base via env (optional):

```bash
# frontend/.env.local
NEXT_PUBLIC_API_BASE=/api/backend
API_UPSTREAM_BASE=http://127.0.0.1:8000
NEXT_PUBLIC_ENABLE_TOKEN_LOGIN=false
```

`NEXT_PUBLIC_ENABLE_TOKEN_LOGIN=true` enables internal token login controls for local/debug use only.

## Same-origin API and authentication

The browser must call the backend through `/api/backend`; it must not call the
Render API origin directly. Next.js proxies this path to the server-only
`API_UPSTREAM_BASE`.

Production Vercel environment:

```env
NEXT_PUBLIC_API_BASE=/api/backend
API_UPSTREAM_BASE=https://client-dash-up.onrender.com
NEXT_PUBLIC_ENABLE_TOKEN_LOGIN=false
```

The production backend must use:

```env
APP_ENV=production
FRONTEND_BASE_URL=https://dash.envidicy.kz
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
ALLOWED_ORIGINS=https://dash.envidicy.kz
```

OAuth callback URLs registered both in the provider console and the backend
provider configuration must point to the Vercel proxy:

- Google: `https://dash.envidicy.kz/api/backend/auth/google/callback`
- Facebook: `https://dash.envidicy.kz/api/backend/auth/facebook/callback`

Facebook uses two intentionally separate OAuth configurations even though the
callback path is the same:

- **Войти через Facebook** (`intent=login`) only signs the user into the
  platform. It uses `FACEBOOK_AUTH_CLIENT_ID`, `FACEBOOK_AUTH_CLIENT_SECRET`,
  and `FACEBOOK_AUTH_REDIRECT_URI`; it does not connect advertising accounts.
  On the first successful login, the backend automatically creates an active
  client user and client workspace, then opens the client portal immediately.
- **Подключить Meta Ads** (`intent=connect`) is available after login for an
  administrator or agency user. It uses `FACEBOOK_CLIENT_ID`,
  `FACEBOOK_CLIENT_SECRET`, `FACEBOOK_REDIRECT_URI`, and
  `FACEBOOK_LOGIN_CONFIG_ID` to grant access to advertising accounts.

Register the callback URL in both Facebook apps/configurations. A normal new
Facebook user does not wait for administrator approval. The automatically
created account is limited to the client portal; agency/admin roles and access
to additional client workspaces remain administrator-managed.

This makes the backend's `ops_session` and `ops_csrf` cookies first-party
cookies on the frontend origin. Do not expose `API_UPSTREAM_BASE` through a
`NEXT_PUBLIC_*` variable.

## Notes

- Dashboard/client-ops logic is split into screen components:
  - `components/views/DashboardView.tsx`
  - `components/views/ClientOperationsView.tsx`
- Shared API/domain types are in `lib/types.ts`.
