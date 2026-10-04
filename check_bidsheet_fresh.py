"""
check_bidsheet_fresh.py — email an alert if today's Bid Sheet isn't archived by 5 PM.

Runs on the Droplet at 5 PM CT on weekdays (cron, wrapped by /opt/alerting/cron-alert).
Looks up TODAY's as-of date (America/Chicago) in the River FOB Snowflake archive.
If today is a trading day and it isn't archived yet, sends a plain "not updated"
email through Microsoft Graph, using the same app-only sendMail pattern as the box's
/opt/alerting/notify.py. That gives someone time to chase Doug's email or paste the
sheet in by hand.

It doesn't depend on the import. It only reads Snowflake, which the desktop Outlook
task (and later the droplet Graph job) fills in, so it still fires when the desktop
is off. That's the case where a day gets missed.

    python check_bidsheet_fresh.py              # check; alert if today is missing
    python check_bidsheet_fresh.py --check       # prove Snowflake + Graph Mail.Send; send nothing
    python check_bidsheet_fresh.py --dry-run     # print the alert instead of sending it
    python check_bidsheet_fresh.py --force-send  # send the alert regardless (tests delivery)

Exit codes: 0 = archived, alert sent, or skipped (weekend/holiday).
            3 = a real error (Snowflake or Graph unreachable). cron-alert reports it
                as a job failure. A missing day exits 0 after sending its own email,
                so cron-alert doesn't send a second one.

Env (the repo .env, already on the droplet):
  USE_SNOWFLAKE + SNOWFLAKE_*        read the archive
  GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET / GRAPH_SENDER
                                     send the mail (any missing key is filled from
                                     /opt/basis-tracker/.env, the box's main copy)
  BIDSHEET_ALERT_TO                  recipient override (comma-separated);
                                     default is DEFAULT_TO below
  BIDSHEET_SKIP_DATES                extra ISO dates to treat as holidays
"""
import base64
import json
import logging
import os
import re
import socket
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"), override=True)
except ImportError:
    pass

import db

GRAPH = "https://graph.microsoft.com/v1.0"
CT = ZoneInfo("America/Chicago")
DEFAULT_TO = "kpostin@jpsi.com,cjacobs@jpsi.com"
BASIS_ENV = Path("/opt/basis-tracker/.env")
GRAPH_KEYS = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER")

# Days the grain market is fully closed, so no Bid Sheet is expected. Extend this
# set each year, or add dates with BIDSHEET_SKIP_DATES. Worst case for a missing
# holiday is one harmless "not updated" email, so list only sure closures.
# Don't add doubtful ones: a wrongly listed date silences a real alert.
HOLIDAYS = {
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bidsheet_fresh")


def parse_env(path):
    """Minimal KEY=VALUE reader, same rules as notify.py: handles quotes,
    'export ', comments, a BOM and CRLF."""
    out = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, _, v = line.partition("=")
        if k.strip():
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _graph_cfg():
    cfg = {k: (os.environ.get(k) or "").strip() for k in GRAPH_KEYS}
    if not all(cfg.values()):
        fill = parse_env(BASIS_ENV)
        for k in GRAPH_KEYS:
            cfg[k] = cfg[k] or (fill.get(k) or "").strip()
    return cfg


def _split(v):
    return [a.strip() for a in re.split(r"[,;\s]+", v or "") if a.strip()]


def _recipients():
    return _split(os.environ.get("BIDSHEET_ALERT_TO")) or _split(DEFAULT_TO)


def _skip_dates():
    return HOLIDAYS | set(_split(os.environ.get("BIDSHEET_SKIP_DATES")))


def _token(cfg):
    import requests
    r = requests.post(
        f"https://login.microsoftonline.com/{cfg['GRAPH_TENANT_ID']}/oauth2/v2.0/token",
        data={"client_id": cfg["GRAPH_CLIENT_ID"],
              "client_secret": cfg["GRAPH_CLIENT_SECRET"],
              "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials"},
        timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Graph auth failed: HTTP {r.status_code} {r.text[:300]}")
    access = r.json().get("access_token")
    if not access:
        raise RuntimeError("Graph auth returned no token")
    return access


def _roles(access):
    p = access.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("roles", [])


def _send(cfg, recips, subject, html):
    import requests
    msg = {"message": {"subject": subject,
                       "body": {"contentType": "HTML", "content": html},
                       "toRecipients": [{"emailAddress": {"address": a}} for a in recips]},
           "saveToSentItems": False}
    r = requests.post(f"{GRAPH}/users/{quote(cfg['GRAPH_SENDER'])}/sendMail",
                      headers={"Authorization": f"Bearer {_token(cfg)}",
                               "Content-Type": "application/json"},
                      data=json.dumps(msg), timeout=30)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"sendMail returned HTTP {r.status_code}: {r.text[:300]}")


def _nice(d):
    # Built by hand: %-d is Linux-only and %#d is Windows-only.
    return f"{d.strftime('%a %b')} {d.day}, {d.year}"


def compose(today, newest, now_ct):
    """-> (subject, html) for the 'not updated' alert."""
    day = _nice(today)
    subject = f"[River FOB] Today's Bid Sheet not entered yet ({day})"
    html = (
        "<div style='font-family:system-ui,sans-serif;font-size:14px;line-height:1.5'>"
        f"<p>Today's River FOB <b>Bid Sheet ({escape(day)})</b> has not been archived "
        f"as of <b>{escape(now_ct.strftime('%I:%M %p').lstrip('0'))} CT</b>.</p>"
        "<p>Doug's email may be late, the import may not have run, or the CBOT "
        "board may not have pulled (the import won't save a day without futures). "
        "To fill it in:</p>"
        "<ul>"
        "<li>Paste it by hand in the app: <b>📝 Inputs</b> tab → <b>Paste daily "
        "tables</b> → <b>Save to archive</b>, or</li>"
        "<li>Wait for the 6:30 PM import retry, which picks up a late email.</li>"
        "</ul>"
        f"<p>Most recent archived date: <b>{escape(newest)}</b>.</p>"
        "<p style='color:#94a3b8;font-size:12px'>Automated check from "
        f"{escape(socket.gethostname())} (check_bidsheet_fresh.py, 5 PM CT weekdays).</p>"
        "</div>")
    return subject, html


def _as_text(html):
    t = html.replace("</p>", "\n").replace("</li>", "\n").replace("<li>", "  - ")
    return re.sub(r"<[^>]+>", "", t).strip()


def main():
    check = "--check" in sys.argv
    dry = "--dry-run" in sys.argv
    force = "--force-send" in sys.argv

    if db._backend() == "sqlite":
        log.error("No shared backend (USE_SNOWFLAKE + SNOWFLAKE_*). Refusing to read SQLite.")
        return 3

    now_ct = datetime.now(CT)
    today = now_ct.date()
    today_iso = today.isoformat()

    try:
        dates = db.list_dates()
    except Exception as e:
        log.error("Could not read the archive from Snowflake: %s", e)
        return 3
    present = today_iso in {str(d)[:10] for d in dates}
    newest = str(dates[0])[:10] if dates else "none"

    if check:
        cfg = _graph_cfg()
        missing = [k for k in GRAPH_KEYS if not cfg[k]]
        if missing:
            log.error("Graph not configured, missing: %s", ", ".join(missing))
            return 3
        try:
            roles = _roles(_token(cfg))
        except Exception as e:
            log.error("%s", e)
            return 3
        ok = "Mail.Send" in roles
        log.info("Snowflake OK: %d dates, newest %s. Today %s archived=%s",
                 len(dates), newest, today_iso, present)
        log.info("Graph OK: roles=%s, Mail.Send=%s", roles, "yes" if ok else "NO")
        log.info("Would send from %s to %s", cfg["GRAPH_SENDER"], ", ".join(_recipients()))
        return 0 if ok else 3

    if not force:
        if today.weekday() >= 5:
            log.info("Weekend (%s). No Bid Sheet expected, skipping.", today_iso)
            return 0
        if today_iso in _skip_dates():
            log.info("Market holiday (%s). No Bid Sheet expected, skipping.", today_iso)
            return 0
        if present:
            log.info("Today %s is archived. OK, no alert.", today_iso)
            return 0

    subject, html = compose(today, newest, now_ct)
    recips = _recipients()
    if dry:
        print(f"DRY RUN, not sending\nTo: {', '.join(recips)}\nSubject: {subject}\n")
        print(_as_text(html))
        return 0

    cfg = _graph_cfg()
    missing = [k for k in GRAPH_KEYS if not cfg[k]]
    if missing:
        log.error("Graph not configured, missing: %s. Alert NOT sent.", ", ".join(missing))
        return 3
    try:
        _send(cfg, recips, subject, html)
    except Exception as e:
        log.error("ALERT SEND FAILED: %s", e)
        return 3
    log.info("Alert sent to %s: today %s is not archived (newest %s).",
             ", ".join(recips), today_iso, newest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
