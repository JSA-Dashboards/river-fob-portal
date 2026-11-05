#!/bin/bash
# Uptime check for the River FOB client API (https://162.243.174.38), reported to healthchecks.io.
#
# Kolten 2026-11-05: "set up the uptime check on healthchecks". Healthchecks only waits for pings, so this script is the probe.
# It runs on the OTHER droplet (137.184.195.51, crontab every 5 minutes), so a dead API droplet is caught too, and pings the
# check `river-fob-api-uptime` with the project ping key in /opt/alerting/alert.conf (the same key cron-alert uses).
#
# The API counts as up when ALL hold (the second attempt, 30 s after a failed first, decides, so a deploy restart or a
# network blip doesn't page):
#   1. GET /v1/health over HTTPS answers 200 with a certificate curl trusts (catches: droplet down, firewall, Caddy, uvicorn);
#   2. its body says "data":"ok" (the API holds the 12 months: catches a start-up load that failed against Snowflake);
#   3. the certificate has more than 24 h left. Let's Encrypt IP certificates last 6 days and Caddy renews them with ~2 days
#      to go, so under 24 h means renewal is failing, days before clients would see a broken certificate.
# Up -> ping the check; down -> ping /fail with the reason (healthchecks emails at once). No ping at all (this droplet or its
# cron down) -> healthchecks emails after the check's period + grace. Each run writes one line to syslog
# (journalctl -t river-fob-api-uptime). Plain crontab line, not cron-alert: healthchecks is the alerting here.
set -u
URL="${API_URL:-https://162.243.174.38}"
HOSTPORT="${URL#https://}:443"
CONF=/opt/alerting/alert.conf
SLUG="${HC_SLUG:-river-fob-api-uptime}"
KEY=$(sed -nE 's/^[[:space:]]*HC_PING_KEY[[:space:]]*=[[:space:]]*"?([^"#]*)"?.*/\1/p' "$CONF" 2>/dev/null | head -1 | tr -d ' ')
BASE=$(sed -nE 's/^[[:space:]]*HC_BASE_URL[[:space:]]*=[[:space:]]*"?([^"#]*)"?.*/\1/p' "$CONF" 2>/dev/null | head -1 | tr -d ' ')
BASE=${BASE:-https://hc-ping.com}

check() {
    local body end left
    body=$(curl -fsS --max-time 20 "$URL/v1/health" 2>&1) || { echo "DOWN: health request failed: ${body:0:200}"; return 1; }
    case "$body" in
        *'"data":"ok"'*) ;;
        *) echo "DOWN: API answers but has no data loaded: ${body:0:200}"; return 1 ;;
    esac
    end=$(echo | timeout 20 openssl s_client -connect "$HOSTPORT" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    [ -n "$end" ] || { echo "DOWN: couldn't read the certificate"; return 1; }
    left=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 3600 ))
    [ "$left" -ge 24 ] || { echo "DOWN: certificate expires in ${left} h: renewal is failing (Caddy on the API droplet)"; return 1; }
    echo "up: health ok, data ok, certificate ${left} h left"
}

msg=$(check)
rc=$?
if [ $rc -ne 0 ]; then
    sleep 30
    msg=$(check)
    rc=$?
fi
/usr/bin/logger -t river-fob-api-uptime "$msg"
[ -n "$KEY" ] || { /usr/bin/logger -t river-fob-api-uptime "no HC_PING_KEY in $CONF: nothing reported"; exit 0; }
[ $rc -eq 0 ] && path="$SLUG" || path="$SLUG/fail"
curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "$msg" "$BASE/$KEY/$path?create=1" \
    || /usr/bin/logger -t river-fob-api-uptime "couldn't reach healthchecks ($path)"
exit 0
