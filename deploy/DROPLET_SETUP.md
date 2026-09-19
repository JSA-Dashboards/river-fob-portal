# River FOB Portal — daily Fastmarkets pull on the DigitalOcean Droplet

Runs `fob_vessel_import.py` once each weekday: pulls the latest Fastmarkets
export-FOB assessments (corn / soybeans / wheat across the tracked origins) and
upserts them into `RIVER_FOB.PUBLIC.fob_vessel_history`, which the portal's
**🚢 FOB Vessel** tab reads. This replaces the local Windows Task Scheduler job
`FobVesselDailyImport`, which silently stopped writing after the Snowflake
cutover. **The Streamlit app still runs on Streamlit Cloud** — this Droplet only
runs the pull and writes to the shared Snowflake DB the app reads.

Shares the same Droplet as the basis-tracker scrape. The job is tiny (one HTTP
pull + a MERGE), so it gets its own lean virtualenv rather than the app's full
UI/chart stack.

Assumes Ubuntu 22.04/24.04. Adjust `APP_DIR` if you clone somewhere other than
`/opt/river-fob-portal` (also edit it at the top of `deploy/run_vessel.sh`).

## 1. Clone + virtualenv + dependencies

```bash
sudo mkdir -p /opt/river-fob-portal && sudo chown "$USER" /opt/river-fob-portal
git clone https://github.com/JSA-Dashboards/river-fob-portal.git /opt/river-fob-portal
cd /opt/river-fob-portal

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
# Jobs-only deps — NOT the app's requirements.txt (no streamlit/pandas/plotly needed).
.venv/bin/pip install -r deploy/requirements-jobs.txt
```

(If you prefer your `git archive HEAD | ssh … tar -x` deploy over a clone, that
works too — just make sure `deploy/requirements-jobs.txt` and the `.venv` land in
`/opt/river-fob-portal`. A plain clone is simplest since the repo is on the org.)

## 2. Secrets — `.env`

`fob_vessel_import.py` reads `/opt/river-fob-portal/.env` via `load_dotenv()`.
Copy your **local** portal `.env` up (it already has everything):

```bash
# from your local machine:
scp ".env" youruser@DROPLET_IP:/opt/river-fob-portal/.env
```

It must contain:
- `USE_SNOWFLAKE=1`
- the full `SNOWFLAKE_*` block — `SNOWFLAKE_ACCOUNT` / `USER` / `PASSWORD` /
  `ROLE` / `WAREHOUSE`, plus **`SNOWFLAKE_DATABASE=RIVER_FOB`** and
  **`SNOWFLAKE_SCHEMA=PUBLIC`** (this data is its own database, *not* `JSA`)
- `FOB_VESSEL_SERVICE_NAME` and `FOB_VESSEL_API_KEY` — the Fastmarkets creds

`DATABASE_URL` is not needed (Snowflake wins on `USE_SNOWFLAKE`), and none of the
app-only secrets (`EDIT_PASSWORD`, `MASSIVE_API_KEY`) matter to this job.

```bash
chmod 600 /opt/river-fob-portal/.env    # keep secrets private
```

## 3. Test it once by hand

```bash
cd /opt/river-fob-portal
chmod +x deploy/run_vessel.sh
# direct run — prints the backend then the refreshed row count:
.venv/bin/python fob_vessel_import.py
# then end-to-end via the wrapper:
./deploy/run_vessel.sh && tail -n 40 logs/fob_vessel_*.log | tail -40
```

Confirm the log ends with `fob_vessel finished … rc=0`, that the backend line
reads `Backend: Snowflake`, and that the row count is non-zero. The FOB Vessel
tab on the Cloud app should show the current date within a minute (no reboot
needed — it's a data update, not a code change).

## 4. Install the cron job

Runs 4:00 PM Central, Mon–Fri — 15 minutes after the basis-tracker scrape
(`45 15`), so the two jobs don't contend. Add it to the user's crontab:

```bash
( crontab -l 2>/dev/null; echo "0 16 * * 1-5 /opt/river-fob-portal/deploy/run_vessel.sh" ) | crontab -
crontab -l    # verify — you should see both the 45 15 (basis) and 0 16 (vessel) lines
```

Fastmarkets publishes/revises across the day; the job refreshes a **trailing
7-day window** each run, so a late or revised assessment still lands even though
it fires once daily.

## 5. Monitoring

```bash
ls -lt /opt/river-fob-portal/logs | head          # newest run first
tail -f /opt/river-fob-portal/logs/fob_vessel_*.log
grep -iE "error|fail|refused|rc=[^0]" /opt/river-fob-portal/logs/fob_vessel_*.log
```

## 6. Retire the old desktop task

Once a Droplet run has landed and the FOB Vessel tab is current, disable the
local Windows Task Scheduler job so the two don't double-write:

- Task Scheduler → **FobVesselDailyImport** → Disable (or Delete).

(Leave `RiverFobDailyImport` alone for now — that's the CIF/freight sheet import,
a separate decision.)

## Updating the code later

```bash
cd /opt/river-fob-portal && git pull
.venv/bin/pip install -r deploy/requirements-jobs.txt   # only if deps changed
```

---

### Notes
- **Backfill:** `fob_vessel_import.py --backfill 2024-08-13 2026-08-12` pulls a
  historical range (Fastmarkets caps each call at ~2 years). Run by hand, not on
  cron.
- The wrapper's `flock` guards against a slow run colliding with the next day's
  trigger.
- Everything writes to the same **Snowflake** DB the Cloud app reads, so no app
  redeploy is needed for the data to appear.
- **cron vs systemd timer:** cron matches the basis-tracker setup on this box. A
  systemd timer (`OnCalendar=Mon..Fri 16:00`, `Persistent=true`) catches missed
  runs after a reboot — ask if you'd rather have that.
