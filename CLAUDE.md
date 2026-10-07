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

`DATABASE_URL` was intentionally left in the secrets as a Supabase rollback
(clear `USE_SNOWFLAKE` and the FOB archive is back on Supabase). That copy is
frozen ~2026-09-03, so the rollback now yields stale data — it can be deleted
once the Supabase project is decommissioned; while present it is inert
(`USE_SNOWFLAKE` wins in `_backend()`).

**`BASIS_DATABASE_URL` was removed from the secrets (2026-09-20).** The River
Bids tab reads bids from Snowflake `JSA.BASIS_TRACKER` now — `bids_data.py`
`configured()` is true on Snowflake alone and `_sf_rows()` does `USE SCHEMA
JSA.BASIS_TRACKER` on the portal's own connection. `BASIS_DATABASE_URL` was only
used by the `_pg_rows()` path (reached only when `USE_SNOWFLAKE` is off), and it
pointed at the retired/frozen ca-central-1 Supabase basis-tracker copy — a latent
trap that would have served stale bids if `USE_SNOWFLAKE` were ever cleared. Do
not re-add it.

## Six tables, not five

`snowflake/setup_standalone.py` migrates Supabase → Snowflake, idempotently
(`CREATE IF NOT EXISTS` + per-table DELETE/INSERT/COMMIT with a row-count check).

`cif_history` · `freight_history` · `calendar_history` · `futures_history` ·
`spreads_history` · `fob_vessel_history`

`fob_vessel_history` (Fastmarkets FOB Vessel tab, ~51k rows) postdates the
original script and was **absent from it** until 2026-09-06 — it would have been
silently skipped. If you add another table, add it to both `DDL` and `COLS`.

**There is also a seventh table, `save_lock`, and it holds no data.** It's a
one-row coordination table that every Snowflake `save_snapshot` UPDATEs to
serialize writers (see "Two writers, no duplicates" below). It's deliberately
**not** in `setup_standalone.py`: there's nothing to migrate, and
`db._begin_save()` creates it on first use. It's owned by `ACCOUNTADMIN` like its
siblings, and the schema's future grants give `RIVER_FOB_ROLE` UPDATE on it. Don't
drop it as clutter.

## Who else reads this data — migration status

| Consumer | Status |
|---|---|
| `basis-tracker-streamlit/river_fob_data.py` | **Snowflake** (Snowflake-only since 6c344a4, 2026-09) |
| `jsa-admin-portal` → `apps/river_fob/db.py` + `bids_data.py` | **Dormant since 2026-10-06**: the portal's River FOB tile now opens this app (was a drifted copy; Snowflake since 2026-09-18) |
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

Scheduled jobs for this portal now (as of 2026-10-04):

| Job | Where | When (CT) |
|---|---|---|
| FOB Vessel pull (`deploy/run_vessel.sh`) | Droplet cron | 4:00 PM weekdays |
| Bid Sheet email import (`fetch_bidsheet_email.py`) | Desktop task `RiverFobBidSheetImport` | every 10 min, 3:30–7:00 PM weekdays |
| Bid Sheet freshness alert (`deploy/run_bidsheet_check.sh`) | Droplet cron | 5:00 PM weekdays |

The droplet cron lines are wrapped in `/opt/alerting/cron-alert`, which emails when a job fails.

## Bid Sheet email import + the futures guard

Daily CIF/freight now comes from Doug Schultz's **Bid Sheet** email (an `.xlsx`
named `MMDDYY.xlsx`, tab `Bid Sheet`, A1:T16), parsed by `bidsheet.py` and
upserted to Snowflake. This is the automated daily path. The in-app
**📝 Inputs → Paste daily tables → Save to archive** stays as the manual path,
for a late sheet or a correction. Both write the same archive, and neither can
duplicate a day (see "Two writers, no duplicates" below). Two front-ends call the same
`bidsheet.save_bidsheet()`: `fetch_bidsheet_email.py` (desktop Outlook COM,
Windows Task `RiverFobBidSheetImport`, live) and `fetch_bidsheet_graph.py`
(droplet Microsoft Graph, pending IT granting `Mail.Read` on app `19283e00`).

Two parsing traps, both load-bearing:

- **The sheet's own futures are useless in the email.** They are live Eikon
  formulas that arrive as `#N/A`/`#NAME?`, so the CBOT board is pulled from
  **Massive** (`massive_futures.futures_for_calendar`) at import time and the
  spreads derived from it — the carry chart's gross-carry shape and its
  net-of-interest overlay both need stored futures/spreads.
- **A1 is unreliable** (seen a day behind the real sheet) — date the snapshot
  from the attachment FILENAME (MMDDYY), email date as fallback, A1 last.

**The futures guard (2026-10-04).** Because the board comes from Massive and not
the sheet, a Massive blip would otherwise archive a CIF/freight-only day with no
carry data, silently. So `save_bidsheet(require_futures=True)` is the default:
it retries the Massive pull a few times (`_live_futures_spreads`) and raises
`bidsheet.FuturesUnavailable` unless **every** commodity has a front-month price,
refusing to commit before `save_snapshot`. Both fetchers catch it, log the
reason, and **`sys.exit(2)`** so the task/cron run is flagged and the next run
retries. `--allow-no-futures` overrides for a deliberate CIF+freight-only save;
the SAVED log line reports the futures count and flags `[futures INCOMPLETE]`.
Massive serves CURRENT prices only, so this is not a way to backfill futures for
an OLD date — you get today's board. When the droplet/Graph job goes live,
`MASSIVE_API_KEY` must be in `/opt/river-fob-portal/.env` or the guard has no
board to check against.

### Two writers, no duplicates

The 📝 paste and the email import both call `db.save_snapshot`, which
**replaces** the date's rows rather than appending them. Re-saving a day never
duplicates it, whichever path saves first. The import also **skips a date that's
already archived** (`--force` overrides). So a day you paste first is never
overwritten by the 4:30/6:30 import, while a paste after the import replaces it,
which is how you correct a day.

**On Snowflake, "replaces" took more than DELETE-then-INSERT (fixed 2026-10-04).**
The connector autocommits every statement, and Snowflake does **not** enforce
the PRIMARY KEYs that `init_db()` declares for SQLite/Postgres. So a paste and an
import saving the same date at the same moment could run DELETE, DELETE, INSERT,
INSERT and leave the day's rows doubled. A save that died partway could also
leave a half-written day. Measured on scratch tables before the fix:

| How the save ran | Existing date | Brand-new date |
|---|---|---|
| autocommit (old code) | **duplicated** | **duplicated** |
| one explicit transaction | OK | **duplicated**: a 0-row DELETE takes no lock |
| transaction + `save_lock` UPDATE first | OK | OK |

**A transaction alone is not enough,** and the brand-new date is the normal case
(the first save of each day). So `_begin_save()` opens a transaction and UPDATEs
the one-row `save_lock` table before touching data. The second writer blocks on
that UPDATE until the first COMMITs, then replaces the date cleanly. Verified on
real data with two simultaneous identical saves of 10/2: one waited 15.8s
against the other's 8.1s, row counts and values were unchanged, and there were
zero duplicate keys. Any new Snowflake write path to these five tables must go
through `save_snapshot`, or call `_begin_save()` itself.

**The Cloud app needs a reboot to pick this up** (pushing doesn't deploy here).
Until it's rebooted, its Save button still runs the old autocommit code, which
never waits on the lock, so the race is closed only once every writer runs the
new `db.py`.

### 5 PM freshness alert

`check_bidsheet_fresh.py` runs on the Droplet at **5:00 PM CT weekdays**
(`deploy/run_bidsheet_check.sh` under `cron-alert`). If today's date (America/Chicago)
isn't archived, it emails **kpostin@ + cjacobs@jpsi.com** from
`basis-tracker@jpsi.com` via Graph app-only `sendMail`, the same pattern as
`/opt/alerting/notify.py`. It reads only Snowflake, never the mailbox, so it still
fires when the desktop that runs the import is off, which is the main way a day
gets missed.

- **Exit codes:** a missing day sends its own plain-language email and exits **0**,
  so `cron-alert` doesn't send a second, crash-style email. Exit **3** means a real
  error (Snowflake or Graph unreachable), which `cron-alert` reports as a failure.
- **A sheet arriving after ~4:50 emails at 5 and still fills by the next
  10-minute poll.** That was chosen (2026-10-04): an early heads-up beat waiting
  until the import window closes at 7 PM.
- **Holidays:** `HOLIDAYS` in the script lists sure full grain-market closures
  through 2027. Extend it yearly, or add `BIDSHEET_SKIP_DATES` to the droplet
  `.env`. List only sure closures: a wrong entry silences a real alert, while a
  missing one costs one harmless email.
- **Recipients:** `DEFAULT_TO` in the script; `BIDSHEET_ALERT_TO` overrides.
- `--check` proves Snowflake + Graph `Mail.Send` and sends nothing. `--dry-run`
  prints the email. `--force-send` sends it regardless, to test delivery.

### Futures check before Save (2026-10-06)

On 10/06 a paste on the admin portal's old copy of this page archived the Bid
Sheet's cached Eikon futures: 12-14% under the market, and only 4 of 8 months.
Because that paste saved first, the email import skipped the day by design.

- **Fix:** the day was re-saved with the basis tracker's 10/06 settlements
  (`JSA.BASIS_TRACKER.FUTURES_PRICES`, captured ~3:54 PM CT). That table is the
  way to repair a past day's futures; Massive only has live prices.
- **The admin portal tile now opens this app** instead of its own copy.
- **Save check:** **💾 Save to archive** runs `_futures_issues()` on click. It
  never runs on render, so the page never waits on Massive. It flags:
  - months with no CBOT price;
  - for today's sheet only, any contract more than `STALE_FUTURES_PCT` (3%) from
    the live Massive board (`_live_cbot_board`, cached 5 min).
- **What the user sees:** nothing is saved, and a warning offers **Save anyway**
  or **Cancel**. This sits on top of the existing CIF/freight review checkbox
  (`_save_guard`).
- **Why 3%:** after the 7 PM reopen the live board is overnight trade, about 2%
  off the close one evening.
- **Test headless** with AppTest, as in the scratchpad's
  `test_riverfob_save_check.py`:
  - set `USE_SNOWFLAKE`, `DATABASE_URL` and `EDIT_PASSWORD` to `""`, so the repo
    `.env` can't point it at the real archive;
  - put the repo on `sys.path`;
  - pick "✏️ Working (live)" in the sidebar first.

## 💵 Net Carry tab (added 2026-10-05)

The basis tracker's **Net Carry** for the river sheet: pick a commodity and a river location and it lays out that
location's FOB value by delivery month, each re-expressed against ONE futures contract, charges interest from a
carry-start month, marks the **top of net carry**, draws the River-style "Cash Fwd Curve" and puts river locations side by
side. It sits right after the commodity sheets in all three views (editable/live, editable/archived, `?view=1`) and follows
the sidebar's date: the latest archived day by default, or the working sheet when "✏️ Working (live)" is picked. In `?view=1`
it is read-only by nature (no PNG/Copy buttons).

**Vendored, not forked.** `net_carry.py`, `net_carry_chart.py`, `net_carry_compare.py`, `carry_rate.py`,
`delivery_period.py`, `data/fed_funds_dff.csv` and `tests/test_net_carry.py` + `tests/test_net_carry_chart.py` are
byte-for-byte copies from `basis-tracker-streamlit`, which is the source of truth. **Never edit them here** (a bug goes
into the tracker first). To re-sync after a change there, run from the tracker repo:

```
python sync_carry_modules.py <path to river-fob-portal> --river           # copy (overwrites the vendored copies)
python sync_carry_modules.py <path to river-fob-portal> --river --check   # list which files differ, change nothing
```

then run the vendored tests. **Always pass `--river`**: it also vendors `river_carry.py` (the archive -> carry inputs), which
the Return to Carry section below needs. Since 2026-10-05 the sync copies the tracker's Return to Carry modules too
(`return_to_carry.py`, `_data`, `_view`, `_block`, their `data/prime_rate.csv` + `data/rtc_futures_*.csv` and the tests with
`tests/fixtures/rtc*.json`) — leave them in. The Net Carry LADDER still uses this portal's own adapter (`net_carry_data.py`),
because it must also read the *working* sheet, which is not in the archive; `river_carry.py` is only for the history.
`rail-fob-portal` vendors the same files (without `--river`).

**This portal's own pieces:** `net_carry_data.py` (the adapter, pure), `net_carry_view.py` (HTML + CSS, pure),
`render_netcarry_tab()` in `app.py` (the Streamlit glue; a `@st.fragment`, so a control in the tab reruns only the tab; it
never raises, a failure becomes a message), `tests/test_net_carry_data.py` + `tests/test_net_carry_view.py`. Run each test
as `python tests/<file>`.

### How a sheet becomes Net Carry's inputs (`net_carry_data.py`)

- **Delivery month.** A sheet column is a month label with no year; the window rolls from the as-of month. The first
  month gets the year that puts it 0-2 months after the sheet's date and every later column is the next such month ('Jan'
  after 'Dec' is next year). A `Spot` column (older sheets) is the prompt bid in the as-of month and stays its own row
  (`Spot Oct 2026`) beside that month's column. Months go to `net_carry` as `Oct 2026`, with the year spelled out, because
  `delivery_period` would otherwise guess the year from the futures contract. A sheet whose first month is *before* its date
  or more than 2 months after it is refused with a message, not placed: in the archive that is 7 sheets (six corn sheets from
  Feb-Mar 2025 whose headers still read Jan..May, and a partial two-month soybean sheet on 2025-11-05). Of the 645
  commodity slots (3 x 215 days) on the days that carry futures, 564 net; 58 have no calendar for that commodity (2023
  days that saved only corn), 16 have a calendar but no futures.
- **Contract per month.** The sheet's own contract code per column ('CZ', 'SF', 'WH'), stored in `calendar_history`. The
  live default is `fob_model.contract_for` (first contract in the crop's cycle on/after the month: corn H K N U Z, soy F H
  K N Q X, wheat H K N U Z), so an October window reads corn CZ CZ CZ CH CH CH CK CK, soy SX SX SF SF SH SH SK SK, wheat WZ
  WZ WZ WH WH WH WK WK. The Bid Sheet's own letters override that (`bidsheet.build_payload`) and older archive sheets
  differ (December corn quoted off CH; Oct 2025 quoted off CU). The code carries no year, so the contract is the
  occurrence of that month letter NEAREST the delivery month (5 months before to 6 after): Dec 2026 + SF = ZSF27, Oct 2025 +
  CU = ZCU25 (not ZCU26), Jan 2026 + WZ = ZWZ25. Roots: corn ZC, soybeans ZS, wheat ZW (Chicago SRW).
- **Futures (the curve).** The CBOT row saved with THAT sheet, $/bu to cents, the first price seen for each contract.
  Archived day: `futures_history` through `db.load_extras` (the `hist_fut` the commodity tabs already load, so the tab makes
  no extra database call). Working sheet: the Inputs-tab CBOT row (`cif_<commodity>["Futures"]`, typed, pasted or pulled
  from Massive). Spreads are not used (they are derived from those prices). **Coverage:** the archive holds futures only
  for sheets saved from 2023-02-01 (215 of the 275 days since; none before; 2026-06-24 has none), and those days say "No
  futures prices were saved with this sheet" instead of guessing. A price outside $1.50-20 is ignored (the import's junk
  band); a contract with no price leaves its months on raw basis, flagged with a red dot. Some saved days hold junk the
  tab cannot see (2025-12-03 wheat prices ZWH26 at ZWZ25's 6.185).
- **FOB.** `compute_fob_grid`, $/bu x 100. A month with no value (a closed reach, a blank CIF) is left out and interest
  runs by calendar days.
- **Rate.** Same as every other Net Carry calc: the default is the effective fed funds rate on the sheet's date (FRED
  DFF, cached 6 h, else the committed snapshot) + 2.25% (`carry_rate.rate_for`), editable in the tab's own box (the widget
  key carries the date, so the default re-derives when the date changes). Interest = reference board price x rate x actual
  days / 360 from the carry-start month (default Oct; if the sheet has no delivery in that month, from its first month).
  **This is not the sheet's "% Full Carry" / "Top Carry" interest** (months x (storage + futures x interest / 12) at the
  sidebar rate): those rows are untouched, the two will not match, and the tab says so in a caption.
- **Reference.** "Front delivery's futures" (default) or "Nearest new-crop" (corn Dec, soy Nov, wheat Jul). If the new-crop
  contract is not priced on the sheet (always for wheat: an 8-month window never reaches ZWN) the tab falls back to the front's
  and says so.
- **Compare.** The default peers are the nearest locations on the same river reach, nearest = smallest gap in tariff factor
  (`default_peers`); STL is alone on its reach so it gets MTV, Cairo, Louisville. Every column is re-based to the main
  location's reference with one interest clock, so the columns compare directly.

### Return to Carry history (added 2026-10-05; Kolten: "we should be able to do all the history ... the numbers are all there ... look in the existing FOB sheet archive for the river")

Under the ladder and chart, and above the comparison, the tab now draws the basis tracker's **Return to Carry** for the chosen
location and commodity: what buying at harvest and storing has paid, crop year by crop year — the shipment-by-month table
(break-even basis, current/best bid and return for each shipment month), headline cards, the season chart, best return by
year and the by-year table, with its own Interest switch (this tab's fed funds + 2.25% by default, bank prime to tie out to the
analyst's report). It is `return_to_carry_block.render(...)` (vendored). The **View** (Net / Gross) radio moved above it and
drives it and the comparison; the section also shows when the selected sheet has no ladder for the location.

- **Where the numbers come from:** the whole FOB archive — `db.fetch_all()` (CIF, freight, calendar for every weekly sheet
  since **2006-09**, 3 queries, cached 1 h) read by `river_carry.py` (vendored): the **weekly nearby FOB** of the location (the
  sheet's own month, or the one after a leading `Spot`; never a later month, so a closed upper-river reach leaves a gap that
  week) quoted off the contract the sheet maps that month to, and every posted month as the shipment table's forward quotes
  (so the table works for ANY archived sheet, e.g. the 2019-20 table as of 2019-10-23). Not the working sheet: it is not saved.
  All 18 locations give 21 crop years of corn and soybeans (soybean average best +33..+47¢, corn +35..+53¢); wheat gets the
  ladder only (the block says so). Illinois River, STL, Ohio and the Lower Mississippi are complete; Quincy, Burlington,
  Davenport, Prairie du Chien and Savage have no freight in winter, so their weeks have gaps.
- **Futures:** `net_carry_data.futures_history(root)` — one query of the tracker's `FUTURES_PRICES` per commodity through
  `bids_data._sf_rows` (the same connection as the River Bids tab), over the analyst-sheet weeks in `data/rtc_futures_*.csv`.
  The portal's own `futures_history` (RIVER_FOB) only starts in 2023-02, so it cannot roll a hedge through 20 crop years.
  **Needs read access to `JSA.BASIS_TRACKER.FUTURES_PRICES` for the role the Cloud app connects with.** The Cloud app runs as
  `RIVER_FOB_SVC` / `RIVER_FOB_ROLE`. Since 2026-10-06 that role has USAGE on `JSA` + `JSA.BASIS_TRACKER` and SELECT on exactly
  the four tables this app reads there: `FUTURES_PRICES` (this block) and `SNAPSHOTS`, `SNAPSHOT_ROWS`, `LOCATION_META` (the
  River Bids tab). From the 10-04 switch to that role until then, both failed on the live app, including the `?view=1` client
  link, with "Object does not exist, or operation cannot be performed". A new cross-read of the basis tracker needs its own
  table grant. Without it the block says it couldn't build the history and the rest of the tab is unaffected.
- **Corn 2007-08:** the stored futures hold no Dec 2007 front contract that autumn; its Oct 3 - Nov 28 prices come from the
  analyst's sheet via the vendored `data/rtc_futures_1996_2006.csv`, so the Dec/Mar roll measures (+17.25). The engine, its
  validation against the analyst's workbooks and the soybean rules live in the tracker (`CLAUDE.md` there).
- **Harvest basis (2026-10-06; Kolten: "add the ability to apply your own harvest basis into the models, but default to the
  calculated method"):** the block's **Harvest basis** switch — Calculated (default) or My own, a number in cents vs Dec / Jan that
  measures the crop year being tracked (the as-of sheet's): the shipment table, and the history when that year has weekly bids; a
  what-if box applies it to every year. Vendored — see the tracker's `CLAUDE.md`. The call passes `scope=f"river|{loc}"` so a number typed
  for one location never follows the user to another (the widget keys carry it); keep passing it.
- **Checks:** `python tests/test_river_carry.py` (the archive -> inputs, pure), `tests/test_return_to_carry*.py` (vendored; the
  `_block` one drives the block headless with AppTest) and the futures-history tests in `tests/test_net_carry_data.py`.

### Traps hit while building it

- Streamlit's `runOnSave` reloads `app.py` but **not imported modules**: after editing `net_carry_*.py` restart the server
  (a stale reload can also throw `module 'snowflake' has no attribute 'connector'`).
- `st.markdown` turns a blank line followed by an indented line into a code block and two `$` into LaTeX: the HTML builders
  return one line and the captions avoid `$`.
- html2canvas (the PNG / Copy buttons) paints `box-shadow: inset` as a solid block, so the top row's accent is a border.
- The tab is eager like every tab: heavy work stays out of it (it reuses the already-loaded snapshot; fed funds is cached).
  The first load after a server restart takes ~30 s; a headless test must wait for `data-test-script-state="notRunning"`.
- `st.components.v1.html` (used by `_snap_toolbar` on EVERY tab) and `use_container_width` log deprecation warnings that
  say "removed after 2026-06-01 / 2025-12-31" yet still work on Streamlit 1.56. The new tab uses `width="stretch"` and wraps
  its toolbar calls, so only its PNG buttons would go if the helper ever breaks.

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
