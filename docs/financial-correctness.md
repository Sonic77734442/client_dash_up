# Financial correctness fixes (2026-09-08)

This change set addresses the business/data/UI and operational findings from the
2026-09-07 audit. Invitation/access-control changes, proxy/rate-limit changes,
metrics-cardinality protection, and CSV formula protection are intentionally
deferred at the user's request. No production secrets or database data are
changed by these code changes.

## Budgets and concurrent edits

- Active budget intervals intersect when they share any calendar date. The
  stores reject partial intersections, containment, and a shared boundary date
  with HTTP 409. Disjoint adjacent periods remain valid.
- An account allocation is checked against **every** overlapping client cap.
  Existing semantics are preserved: its full amount counts against each
  overlapping period; this change does not introduce prorated allocations.
- Transfers validate the resulting allocation before changing balances or
  history. An unsuccessful transfer leaves both sides unchanged.
- A client/account PATCH writes only the requested fields. Saving a note or
  sync metadata does not restore a previously read status after another request
  has archived the object.

## Provider data and arithmetic

Provider metrics are validated for the entire returned batch before any row is
ingested or its freshness marker advanced. Malformed/non-finite/negative values,
fractional counters, and values outside the supported database ranges cause a
validation error; the last correctly stored data remains intact. Valid zero is
still accepted. Money uses Decimal with the existing two-place HALF_UP rounding.
Built-in sync fetchers require spend, impressions, and clicks. Legacy custom
fetchers can still omit a metric key to mean zero; explicitly invalid values do
not get this compatibility treatment. Optional conversions may remain absent.

CTR is a fraction with separate eight-decimal precision, not a money value:
25 clicks / 10000 impressions produces `0.0025`, displayed as `0.25%`.
Weighted rates are recalculated from counts rather than averaged from row rates.

## Currency-aware API contract

Aggregation and overview responses add `currency` to metric buckets and a
`totals_by_currency` array. Existing single-currency monetary fields remain
numbers. If rows contain different currencies, the combined `spend`, `cpc`, and
`cpm` are `null` (not zero); non-monetary counts and CTR remain meaningful.

For example, 100 USD and 50000 KZT produce separate USD/KZT totals, not 50100 in
an unspecified currency. No exchange rates are inferred. Agency filtering is
applied before rebuilding both the totals and the currency breakdown.

`spend_summary` and `budget_summary` also carry their currency. A mixed-currency
scope, or legacy data whose spend currency does not match its budget, has no
scalar budget comparison or forecast. In these cases `budget_summary` includes
`unavailable_reason` (`mixed_currencies` or `currency_mismatch`). Operational
cost comparisons use only accounts in the same currency cohort.

Backend and frontend should be released together: older consumers must not
coerce these `null` monetary values to zero. API shape snapshots and regression
tests cover the additive fields and mixed-currency behavior.

## Frontend

- A late response from a previously selected client cannot overwrite the
  current client's page. Loading clears obsolete data; regressions reverse the
  completion order of requests in a single browser session.
- Graphs receive their actual currency rather than assuming KZT.
- Agency totals and spend shares are grouped by currency.
- The budget table compares the selected interval's spend to its expected
  fraction of the full budget, using inclusive UTC calendar days and the same
  90–110% pacing band as the backend. It no longer classifies pace solely by
  total budget usage.
- The dashboard uses the backend's operational recommendations rather than a
  fixed nominal CPC threshold that meant different things in different currencies.
- Dashboard monetary labels prioritize the API bucket's explicit currency,
  including `null` for mixed currencies, over the client's configured default.
- Provider-budget capability discovery no longer displays an unavailable panel
  solely because its history endpoint returned 404. A temporary history failure
  and already-loaded history remain visible; server permissions are unchanged.

## Docker, backups, and SQLite

The API image includes PostgreSQL client tools from Debian trixie (major 17).
Backup/restore helpers check tool availability and version compatibility before
work. A backup is published only after successful dump and archive inspection.
Restore uses one transaction, so a failed restore rolls back its partial changes.
Restore still requires explicit target-replacement confirmation and should be
verified only against a disposable database, never production.

A newer pg_dump can read an older server, but the generated SQL is not guaranteed
to restore to an older server major. The helper warns about this during backup
and refuses a backward-major restore. Use matching client tools when a backup
must restore to the source server's older major, or use a supported newer target.
The Docker image is not itself a backup schedule or off-host backup storage.

Shell scripts are checked out with LF through `.gitattributes`, including on
Windows, so their Linux entrypoints remain executable. SQLite connections close
deterministically after commit/rollback, including schema initialization.

## Regression checks

Critical store regressions are parametrized for persistent SQLite and a separate
PostgreSQL supplied through `TEST_DATABASE_URL`. **Never point this variable at
production**: existing PostgreSQL tests truncate the test database's tables.

```text
python -m pytest -q
python scripts/check_migrations.py
cd frontend
npm run lint
npm run typecheck
npm run build
npm run test:smoke
```

The frontend also has focused finance-helper/relay and mocked-browser configs
for isolating failures without live provider or database calls. Real backup
verification should check restored row contents and rollback after an induced
restore failure, in addition to validating that an archive can be listed.

Verified locally for this change set: the full backend suite passed **569 tests**
with one expected SQLite-side skip of a PostgreSQL-specific constraint test,
using a disposable PostgreSQL 16. After the final empty-client currency fix,
**25 metric/contract/visibility checks** passed again across the supported stores.
Migration sanity passed for all 21 migration files. A separate PostgreSQL 17
dump/restore roundtrip preserved source rows; an induced restore failure rolled
back all target changes. The test databases were isolated from Render.

The final Docker image also built successfully with Python 3.12.14 and PostgreSQL
client tools 17.11. Its actual CMD honored a test `PORT` override; `/healthz` and
`/readyz` passed with all readiness checks true. Linux shell syntax checks passed.
The disposable Docker smoke/backup containers have been removed.

Frontend lint, standalone typecheck, and the production build passed. The
focused finance-helper/relay suite passed all 7 checks. These checks used the
same source as the final production build; typecheck ran after Next generated
its route types.

The final standard `npm run test:smoke` passed **63 tests**, with no failures or
skips, against the production frontend build and an isolated local SQLite API.
This includes reversed client-response ordering, currency-aware reports and
dashboards, hidden unavailable provider controls, and preserved history after
404/503 refresh failures. No live advertising-provider writes were performed.
