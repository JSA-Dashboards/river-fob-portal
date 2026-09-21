#!/usr/bin/env bash
# warm_ping.sh — keep the River FOB *view* app warm on Streamlit Community Cloud
# so clients don't hit the "waking up" cold-start delay.
#
# Community Cloud sleeps an app that gets no traffic, and once asleep it needs a
# manual "wake up" CLICK to come back — a curl can't click. So the reliable fix
# is to ping often enough that the app never sits idle long enough to sleep. Cron
# runs this every ~10 minutes, around the clock.
#
# Deliberately NOT wrapped in /opt/alerting/cron-alert: a transient curl blip (a
# network hiccup, a brief Streamlit deploy) must not page anyone. It always exits
# 0 and just logs the HTTP status; check the log if you want to confirm it's live.
set -uo pipefail

URL="https://river-fob.streamlit.app/?view=1"
LOG_DIR="/opt/river-fob-portal/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/warm_ping.log"

# -sSL: quiet but keep errors, follow redirects. --max-time guards a hung wake.
code=$(curl -sSL -o /dev/null -w '%{http_code}' --max-time 90 "$URL" 2>/dev/null || echo "000")
printf '%s GET -> %s\n' "$(date -Is)" "$code" >> "$LOG"

# Keep the log bounded (one line per run, ~144/day).
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

exit 0
