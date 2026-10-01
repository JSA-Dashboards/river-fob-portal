"""
fetch_bidsheet_email.py — INTERIM desktop fetcher.

Finds Doug Schultz's newest daily **Bid Sheet** email in Outlook (via Outlook
COM, like vessel-lineup's fetcher), grabs the .xlsx attachment, and upserts its
CIF + barge-freight into the River FOB Snowflake archive via `bidsheet`.

This is the desktop stop-gap (no IT needed). The droplet/Graph version will
replace it once Graph `Mail.Read` is granted (see deploy/IT_REQUEST_mail_read.md);
both front-ends call the same `bidsheet.save_bidsheet()`.

Run daily via Windows Task Scheduler, late afternoon (Doug sends ~4pm CT):
    C:\\Python314\\python.exe "<repo>\\fetch_bidsheet_email.py"
Needs: pywin32 (Outlook COM), the repo .env (USE_SNOWFLAKE + SNOWFLAKE_*).
Pass --force to re-save a date that's already archived (e.g. a corrected sheet).
"""
import os
import sys
import tempfile
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"), override=True)
except ImportError:
    pass

import bidsheet
import db

SENDER_FILTER = "schultz"     # matches Doug Schultz / dschultz@jpsi.com
MAX_SCAN = 40                 # how many recent sender emails to look through

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bidsheet_fetch")


def _newest_bidsheet_xlsx():
    """Return (tmp_path, subject, received) for the newest Outlook email from the
    sender that carries an .xlsx with a parseable 'Bid Sheet' tab, else (None,..)."""
    import win32com.client
    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    items = ns.GetDefaultFolder(6).Items          # 6 = Inbox
    f = SENDER_FILTER.lower()
    try:
        items = items.Restrict(
            f"(\"urn:schemas:httpmail:fromname\" LIKE '%{f}%' OR "
            f"\"urn:schemas:httpmail:fromemail\" LIKE '%{f}%')")
    except Exception:
        pass
    items.Sort("[ReceivedTime]", True)            # newest first

    tmp = os.path.join(tempfile.gettempdir(), "bidsheet_fetch_tmp.xlsx")
    scanned = 0
    for item in items:
        if scanned >= MAX_SCAN:
            break
        try:
            if item.Class != 43:                  # 43 = olMail
                continue
        except Exception:
            continue
        scanned += 1
        try:
            atts = item.Attachments
        except Exception:
            continue
        for att in atts:
            if not str(getattr(att, "FileName", "")).lower().endswith(".xlsx"):
                continue
            try:
                att.SaveAsFile(tmp)
                bidsheet.parse_bidsheet(tmp)       # raises unless it's a Bid Sheet
            except Exception:
                continue
            return tmp, getattr(item, "Subject", "?"), getattr(item, "ReceivedTime", "?")
    return None, None, None


def main():
    force = "--force" in sys.argv
    if db._backend() == "sqlite":
        log.error("No shared backend (set USE_SNOWFLAKE + SNOWFLAKE_*) — refusing SQLite.")
        sys.exit(1)
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        log.error("pywin32 not installed — run: pip install pywin32")
        sys.exit(1)

    tmp, subject, received = _newest_bidsheet_xlsx()
    if not tmp:
        log.info("No Bid Sheet email found in the last %d from '%s' — nothing to do.",
                 MAX_SCAN, SENDER_FILTER)
        return
    as_of, *_ = bidsheet.parse_bidsheet(tmp)
    archived = as_of.isoformat() in {str(d)[:10] for d in db.list_dates()}
    if archived and not force:
        log.info("Newest Bid Sheet is %s (email '%s', %s) — already archived; "
                 "nothing to do. Use --force to re-save.", as_of, subject, received)
        return
    res = bidsheet.save_bidsheet(tmp, commit=True)
    log.info("SAVED %s from '%s' (%s): %d CIF, %d freight rows%s",
             res["as_of"], subject, received, res["n_cif"], res["n_frt"],
             " [UPDATED existing]" if res["was_present"] else " [new]")


if __name__ == "__main__":
    main()
