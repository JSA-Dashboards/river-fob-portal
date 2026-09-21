#!/usr/bin/env bash
# warm_ping.sh — keep the River FOB *view* app warm on Streamlit Community Cloud
# so clients don't hit the "waking up" cold-start delay.
#
# Community Cloud sleeps an app that gets no traffic, and once asleep it needs a
# manual "wake up" CLICK to come back — a curl can't click. So the reliable fix
# is to ping often enough that it never sits idle long enough to sleep. Cron runs
# this every ~10 minutes, around the clock.
#
# The view URL sits behind Streamlit's session/auth edge: a plain GET returns 303
# and a naive `curl -L` loops forever (the edge, not the container). Carrying
# cookies through the handshake with a fresh throwaway jar completes it in ~3
# redirects to a real 200 that reaches the app CONTAINER — which is what actually
# keeps it awake.
#
# Deliberately NOT wrapped in /opt/alerting/cron-alert: a transient blip (network
# hiccup, a brief Streamlit deploy) must not page anyone. Always exits 0 and just
# logs the HTTP status; tail the log if you want to confirm it's live.
set -uo pipefail

URL="https://river-fob.streamlit.app/?view=1"
LOG_DIR="/opt/river-fob-portal/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/warm_ping.log"

JAR="$(mktemp)"
code=$(curl -sS -c "$JAR" -b "$JAR" -L --max-redirs 15 --max-time 90 \
            -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null || echo "000")
rm -f "$JAR"
printf '%s GET -> %s\n' "$(date -Is)" "$code" >> "$LOG"

# Keep the log bounded (one line per run, ~144/day).
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

exit 0
