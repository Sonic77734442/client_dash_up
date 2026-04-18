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
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

Or set API base + token in the top session controls in UI and press `Save`.

## Notes

- Dashboard/client-ops logic is split into screen components:
  - `components/views/DashboardView.tsx`
  - `components/views/ClientOperationsView.tsx`
- Shared API/domain types are in `lib/types.ts`.
