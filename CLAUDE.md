# River FOB Portal

CIF NOLA, barge freight and computed FOB values by river reach. Only the two
independent inputs are stored — CIF basis and barge freight, keyed by as-of
date. Everything downstream (FOB, cash vs delivery, carry) is **recomputed on
read**, so history stays small and always reflects the current formulas.

## Backend: Snowflake `RIVER_FOB`, as of 2026-09-06

`db.py` picks a backend in `_backend()`:

1. **Snowflake** when `USE_SNOWFLAKE` is truthy — wins even if `DATABASE_URL`
   is still set, so the cutover is a single flag
2. Postgres when `DATABASE_URL` is a `postgres(ql)://` URL
3. SQLite otherwise (local file)

The archive lives in its own **`RIVER_FOB.PUBLIC` database** — deliberately not
a schema inside `JSA`, because the portal owns its own data.

Required secrets on the deployed app:

```
USE_SNOWFLAKE = "1"
SNOWFLAKE_DATABASE = "RIVER_FOB"     # NOT JSA
SNOWFLAKE_SCHEMA   = "PUBLIC"
SNOWFLAKE_ACCOUNT / USER / PASSWORD / ROLE / WAREHOUSE
```

`_sf_connect()` has **no defaults** — it passes `None` for anything unset, so
omitting `SNOWFLAKE_DATABASE` yields a connection with no database at all.
Setting `SNOWFLAKE_SCHEMA` is correct here; this is a single-purpose app, unlike
the two portals where it must stay unset.

`DATABASE_URL` is intentionally left in the secrets as a rollback: clear
`USE_SNOWFLAKE` and the app is back on Supabase immediately.

## Six tables, not five

`snowflake/setup_standalone.py` migrates Supabase → Snowflake, idempotently
(`CREATE IF NOT EXISTS` + per-table DELETE/INSERT/COMMIT with a row-count check).

`cif_history` · `freight_history` · `calendar_history` · `futures_history` ·
`spreads_history` · `fob_vessel_history`

`fob_vessel_history` (Fastmarkets FOB Vessel tab, ~51k rows) postdates the
original script and was **absent from it** until 2026-09-06 — it would have been
silently skipped. If you add another table, add it to both `DDL` and `COLS`.

## Three other apps still read this data from Supabase

Not yet migrated. They connect with raw `psycopg2` and have no backend
abstraction:

| Consumer | Reads via |
|---|---|
| `jsa-admin-portal` → `apps/river_fob/db.py` | **has no Snowflake code at all** — the `_backend`/`_SFConn`/`_sf_connect` block exists only in this repo |
| `apps/basis_tracker/river_fob_data.py` (both repos) | `RIVER_DATABASE_URL` |
| `apps/rail_fob/river_data.py` | `RIVER_DATABASE_URL` |

**Supabase cannot be retired until those three move.** When porting, note the
collision: the admin portal sets `SNOWFLAKE_DATABASE = "JSA"` globally, but this
data lives in `RIVER_FOB`. The portal already solved the same shape for Postgres
by renaming to `RIVERFOB_DATABASE_URL`; the Snowflake path needs equivalent
treatment or River FOB will look in `JSA` and find nothing — silently, with
empty tabs and no error.

## Deployment

- Branch `master`, main file `app.py`, Python 3.14
- Live at `river-fob.streamlit.app`
- `EDIT_PASSWORD` gates editing and downloads; `?view=1` is the read-only
  client link and needs no password
- `FOB_VESSEL_API_KEY` / `FOB_VESSEL_SERVICE_NAME` — Fastmarkets creds for the
  FOB Vessel tab; without them that tab reports missing credentials
- **Pushing to GitHub does not deploy.** This repo moved into the
  `JSA-Dashboards` org and Streamlit still has the app registered under the old
  owner path — the webhook returns `200 OK` and does nothing. Push, then
  **Manage app → ⋮ → Reboot app**.
