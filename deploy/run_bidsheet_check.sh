#!/usr/bin/env bash
# run_bidsheet_check.sh: 5 PM freshness check for the daily Bid Sheet, run by cron
# on the Droplet.
#
# Runs check_bidsheet_fresh.py. If today's Bid Sheet isn't in the River FOB archive
# by 5 PM CT on a trading day, it emails the "not updated" alert itself and exits 0,
# so cron-alert doesn't send a second email. A non-zero exit means a real error
# (Snowflake or Graph unreachable), and cron-alert reports that as a job failure.
# flock prevents overlap. Output goes to a per-run log, pruned after 30 days.
# Mirrors run_bidsheet.sh.
set -uo pipefail

APP_DIR="/opt/river-fob-portal"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/bidsheet_check_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.bidsheet_check.lock"
if ! flock -n 9; then
    echo "$(date -Is) another bidsheet check is in progress, skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== bidsheet check start $(date -Is) ==="
    "$VENV/bin/python" check_bidsheet_fresh.py
    rc=$?    # capture immediately, before any other command (e.g. date)
    echo "=== bidsheet check finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'bidsheet_check_*.log' -mtime +30 -delete 2>/dev/null || true
exit "$rc"
