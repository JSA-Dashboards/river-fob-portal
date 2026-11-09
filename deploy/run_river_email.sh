#!/usr/bin/env bash
# run_river_email.sh: the River FOB daily email, run by cron on the Droplet every 10 minutes, 4:05 to 7:55 PM CT on weekdays.
#
# Runs river_email.py, which sends at most once a day and does nothing until RIVER_EMAIL_BCC or RIVER_WA_CHATS is set in the repo .env.
# A non-zero exit means repeated failures, and cron-alert reports it. flock prevents overlap. One log a day, pruned after 30 days.
set -uo pipefail

APP_DIR="/opt/river-fob-portal"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/river_email_$(date +%Y%m%d).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.river_email.lock"
if ! flock -n 9; then
    echo "$(date -Is) another river email run is in progress, skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== river email start $(date -Is) ==="
    "$VENV/bin/python" river_email.py
    rc=$?    # capture immediately, before any other command (e.g. date)
    echo "=== river email finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'river_email_*.log' -mtime +30 -delete 2>/dev/null || true
exit "$rc"
