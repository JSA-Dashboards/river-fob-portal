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

## Who else reads this data — migration status

| Consumer | Status |
|---|---|
| `basis-tracker-streamlit/river_fob_data.py` | **Snowflake** (Snowflake-only since 6c344a4, 2026-09) |
| `jsa-admin-portal` → `apps/river_fob/db.py` + `bids_data.py` | **Snowflake** (migrated 2026-09-18) |
| `jsa-admin-portal` → `apps/rail_fob/river_data.py` + `rail_data.py` | **Snowflake** (migrated 2026-09-18) |
| `jsa-admin-portal` → `apps/basis_tracker/*` | retired redirect stub — never reaches a DB |
| **standalone `rail-fob-portal`** (`rail_data.py` + `river_data.py`) | **Snowflake** (migrated 2026-09-18) |

**All consumers are now on Snowflake — Supabase can be decommissioned** once the
migrated apps are deployed and verified. Both portals that cross-read pin their
own database/schema per module (self-contained `_sf_connect`), and the
`*_DATABASE_URL` secrets survive only as a `USE_SNOWFLAKE`-off rollback.

**The `SNOWFLAKE_DATABASE` collision when porting:** the admin portal sets
`SNOWFLAKE_DATABASE = "JSA"` globally, but this data lives in `RIVER_FOB`. Every
migrated module pins `database="RIVER_FOB", schema="PUBLIC"` at connect time
(basis-tracker data pins `JSA`/`BASIS_TRACKER`) — miss that and River FOB looks
in `JSA`, finds nothing, and shows empty with no error.

## Scheduled jobs — on the Droplet, not the desktop (as of 2026-09-19)

Both Windows Task Scheduler jobs on Kolten's desktop are **retired (Disabled)**:

| Old desktop task | Ran | Replaced by |
|---|---|---|
| `FobVesselDailyImport` | `fob_vessel_import.py` (Fastmarkets FOB Vessel → Snowflake) | **Droplet cron** — `deploy/run_vessel.sh`, `0 16 * * 1-5`, on the shared basis-tracker Droplet at `/opt/river-fob-portal`. See `deploy/DROPLET_SETUP.md`. |
| `RiverFobDailyImport` | `daily_fob_import.py` (CIF/freight from the local Excel workbook) | **Nothing — retired.** The `JSA FOB Sheet …xlsx` workbook is no longer used at all; daily CIF/freight/futures entry is done **in-app** (📝 Inputs tab → "Paste daily tables" → Save to archive → Snowflake). |

So there is no workbook-based auto-import anywhere. `daily_fob_import.py`,
`import_fob_master.py`, and the workbook backfill scripts are historical only.
The only scheduled job for this portal is the Droplet's FOB Vessel pull.

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
