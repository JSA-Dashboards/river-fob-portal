"""
fetch_bidsheet_graph.py — OFF-DESKTOP fetcher (Microsoft Graph).

Reads the shared mailbox via Graph app-only OAuth (the same "Basis Tracker" app
registration already used for sending — client 19283e00, now with Mail.Read),
finds Doug Schultz's newest Bid Sheet email, downloads the .xlsx attachment, and
upserts its CIF + barge freight into the River FOB Snowflake archive via
`bidsheet`. Runs on the Droplet via cron — no desktop, no Outlook.

Mailbox = GRAPH_BIDSHEET_MAILBOX, else GRAPH_SENDER (= basis-tracker@jpsi.com).
Needs in the .env: GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET
(+ GRAPH_SENDER), plus USE_SNOWFLAKE + SNOWFLAKE_* for RIVER_FOB.

    python fetch_bidsheet_graph.py            # fetch + save (idempotent)
    python fetch_bidsheet_graph.py --check     # verify auth + find the email, save nothing
    python fetch_bidsheet_graph.py --force     # re-save even if the date is archived
    python fetch_bidsheet_graph.py --allow-no-futures  # save CIF/freight even if the
                                               # CBOT board didn't pull (no carry data)

By default the save REFUSES to commit unless the live CBOT board pulled from
Massive (so a Massive blip can't archive a carry-less day); it exits 2 so the
cron run is flagged and the next one retries. Pass --allow-no-futures to override.
"""
import os
import sys
import json
import base64
import tempfile
import logging
import datetime as dt
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"), override=True)
except ImportError:
    pass

import bidsheet
import db

SENDER_FILTER = "schultz"
GRAPH = "https://graph.microsoft.com/v1.0"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bidsheet_graph")


def _cfg(k):
    return (os.environ.get(k) or "").strip()


def _mailbox():
    return _cfg("GRAPH_BIDSHEET_MAILBOX") or _cfg("GRAPH_SENDER") or "basis-tracker@jpsi.com"


def _token():
    import msal
    tenant, client, secret = (_cfg("GRAPH_TENANT_ID"), _cfg("GRAPH_CLIENT_ID"),
                              _cfg("GRAPH_CLIENT_SECRET"))
    if not (tenant and client and secret):
        raise RuntimeError("Graph not configured — need GRAPH_TENANT_ID, "
                           "GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET in the .env.")
    app = msal.ConfidentialClientApplication(
        client_id=client,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=secret,
    )
    tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    access = tok.get("access_token")
    if not access:
        raise RuntimeError(f"Graph auth failed: {tok.get('error_description') or tok}")
    return access


def _roles(access):
    """The app roles granted in the token (so --check can confirm Mail.Read)."""
    try:
        p = access.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("roles", [])
    except Exception:
        return []


def _get(url, access):
    import requests
    r = requests.get(url, headers={"Authorization": f"Bearer {access}"}, timeout=60)
    r.raise_for_status()
    return r.json()


def _newest_bidsheet(access, mailbox):
    """-> (tmp_path, as_of, subject, received) for the newest Bid Sheet email from
    the sender, else (None, …). Filters the sender in Python (robust)."""
    mb = urllib.parse.quote(mailbox)
    q = ("?$top=60&$orderby=receivedDateTime desc"
         "&$select=id,subject,receivedDateTime,hasAttachments,from")
    msgs = _get(f"{GRAPH}/users/{mb}/messages{q}", access).get("value", [])
    tmp = os.path.join(tempfile.gettempdir(), "bidsheet_graph_tmp.xlsx")
    for m in msgs:
        if not m.get("hasAttachments"):
            continue
        addr = (m.get("from") or {}).get("emailAddress") or {}
        who = f"{addr.get('name', '')} {addr.get('address', '')}".lower()
        if SENDER_FILTER not in who:
            continue
        atts = _get(f"{GRAPH}/users/{mb}/messages/{m['id']}/attachments", access).get("value", [])
        for a in atts:
            name = str(a.get("name", ""))
            if not name.lower().endswith(".xlsx") or "contentBytes" not in a:
                continue
            recv = m.get("receivedDateTime")                    # ISO Z
            recv_d = dt.date.fromisoformat(recv[:10]) if recv else None
            as_of = bidsheet.date_from_filename(name) or recv_d  # A1 is unreliable
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(a["contentBytes"]))
            try:
                bidsheet.parse_bidsheet(tmp, as_of=as_of)        # raises unless Bid Sheet
            except Exception:
                continue
            return tmp, as_of, m.get("subject", "?"), recv
    return None, None, None, None


def main():
    force = "--force" in sys.argv
    check = "--check" in sys.argv
    allow_no_fut = "--allow-no-futures" in sys.argv
    if db._backend() == "sqlite":
        log.error("No shared backend (USE_SNOWFLAKE + SNOWFLAKE_*) — refusing SQLite.")
        sys.exit(1)

    access = _token()
    mailbox = _mailbox()
    if check:
        log.info("auth OK — token roles: %s", _roles(access) or "(none — Mail.Read missing?)")
        log.info("reading mailbox: %s", mailbox)

    tmp, as_of, subject, received = _newest_bidsheet(access, mailbox)
    if not tmp:
        log.info("No Bid Sheet email found in %s from '%s' — nothing to do "
                 "(is Doug's email delivered to that mailbox?).", mailbox, SENDER_FILTER)
        return
    if check:
        log.info("Found newest Bid Sheet: %s (email '%s', %s) — NOT saving (--check).",
                 as_of, subject, received)
        return

    archived = as_of.isoformat() in {str(d)[:10] for d in db.list_dates()}
    if archived and not force:
        log.info("Newest Bid Sheet is %s (email '%s', %s) — already archived; "
                 "nothing to do. Use --force to re-save.", as_of, subject, received)
        return
    try:
        res = bidsheet.save_bidsheet(tmp, as_of=as_of, commit=True,
                                     require_futures=not allow_no_fut)
    except bidsheet.FuturesUnavailable as e:
        log.error("%s", e)
        sys.exit(2)                               # flagged; the next cron run retries
    log.info("SAVED %s from '%s' (%s): %d CIF, %d freight, %d futures%s%s",
             res["as_of"], subject, received, res["n_cif"], res["n_frt"], res["n_fut"],
             "" if res["futures_complete"] else " [futures INCOMPLETE]",
             " [UPDATED existing]" if res["was_present"] else " [new]")


if __name__ == "__main__":
    main()
