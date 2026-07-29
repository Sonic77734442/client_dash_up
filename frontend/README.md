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
FRONTEND_BASE_URL=https://client-dash-up.vercel.app
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
ALLOWED_ORIGINS=https://client-dash-up.vercel.app
```

OAuth callback URLs registered both in the provider console and the backend
provider configuration must point to the Vercel proxy:

- Google: `https://client-dash-up.vercel.app/api/backend/auth/google/callback`
- Facebook: `https://client-dash-up.vercel.app/api/backend/auth/facebook/callback`

This makes the backend's `ops_session` and `ops_csrf` cookies first-party
cookies on the frontend origin. Do not expose `API_UPSTREAM_BASE` through a
`NEXT_PUBLIC_*` variable.

## Notes

- Dashboard/client-ops logic is split into screen components:
  - `components/views/DashboardView.tsx`
  - `components/views/ClientOperationsView.tsx`
- Shared API/domain types are in `lib/types.ts`.
