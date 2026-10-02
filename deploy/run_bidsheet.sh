#!/usr/bin/env bash
# run_bidsheet.sh — daily Bid Sheet email import, invoked by cron on the Droplet.
#
# Runs fetch_bidsheet_graph.py: reads the basis-tracker shared mailbox via Graph,
# finds Doug Schultz's newest Bid Sheet email, and upserts CIF+freight into the
# River FOB Snowflake archive. Idempotent (skips a date already archived). flock
# prevents overlap; output is logged per-run and pruned at 30d. Mirrors run_vessel.sh.
set -uo pipefail

APP_DIR="/opt/river-fob-portal"
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/bidsheet_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.bidsheet.lock"
if ! flock -n 9; then
    echo "$(date -Is) another bidsheet run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== bidsheet start $(date -Is) ==="
    "$VENV/bin/python" fetch_bidsheet_graph.py
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    echo "=== bidsheet finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'bidsheet_*.log' -mtime +30 -delete 2>/dev/null || true
exit "$rc"
