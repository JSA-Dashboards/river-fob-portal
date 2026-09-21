#!/usr/bin/env bash
# run_vessel.sh — daily Fastmarkets FOB Vessel pull, invoked by cron on the Droplet.
#
# Runs fob_vessel_import.py (refreshes a trailing 7-day window of export-FOB
# assessments → Snowflake RIVER_FOB.PUBLIC.fob_vessel_history). fob_vessel_import
# calls load_dotenv() against its own directory, so we cd into APP_DIR first and it
# reads .env from there. flock prevents overlapping runs. Output is logged per-run
# and pruned at 30d. Mirrors basis-tracker's deploy/run_daily.sh.
#
# Cron installs this; adjust APP_DIR to wherever the repo is cloned.
set -uo pipefail

APP_DIR="/opt/river-fob-portal"              # <-- clone path (edit me)
VENV="$APP_DIR/.venv"                         # virtualenv created during setup
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fob_vessel_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

# Single-instance guard: skip silently if a run is already going.
exec 9>"$LOG_DIR/.fob_vessel.lock"
if ! flock -n 9; then
    echo "$(date -Is) another fob_vessel run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== fob_vessel start $(date -Is) ==="
    "$VENV/bin/python" fob_vessel_import.py
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    echo "=== fob_vessel finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

# Keep 30 days of logs.
find "$LOG_DIR" -name 'fob_vessel_*.log' -mtime +30 -delete 2>/dev/null || true

# Surface the real exit code to cron, so a failed run can alert via MAILTO.
exit "$rc"
