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

By default the save REFUSES to commit unless the live CBOT board pulled from
Massive (so a Massive blip can't archive a carry-less day); it exits 2 so the
task is flagged and the next run retries. Pass --allow-no-futures to override
and save CIF/freight only.
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
MAX_SCAN = 120                # how many recent inbox items to look through

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bidsheet_fetch")


def _newest_bidsheet_xlsx():
    """Return (tmp_path, subject, received) for the newest Outlook email from the
    sender that carries an .xlsx with a parseable 'Bid Sheet' tab, else (None,..)."""
    import win32com.client
    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    items = ns.GetDefaultFolder(6).Items          # 6 = Inbox
    items.Sort("[ReceivedTime]", True)            # newest first
    f = SENDER_FILTER.lower()

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
        # Match the sender in PYTHON — Outlook's DASL sender Restrict undercounts
        # (silently misses matching mail), so we scan and filter here instead.
        try:
            who = (str(item.SenderName or "") + " "
                   + str(item.SenderEmailAddress or "")).lower()
        except Exception:
            who = ""
        if f not in who:
            continue
        try:
            atts = item.Attachments
        except Exception:
            continue
        for att in atts:
            name = str(getattr(att, "FileName", ""))
            if not name.lower().endswith(".xlsx"):
                continue
            recv = getattr(item, "ReceivedTime", None)
            # The date comes from the FILENAME (MMDDYY), not the sheet's A1 cell
            # (A1 has been a day behind the real sheet). Email date as a fallback.
            as_of = bidsheet.date_from_filename(name) or (recv.date() if recv else None)
            try:
                att.SaveAsFile(tmp)
                bidsheet.parse_bidsheet(tmp, as_of=as_of)   # raises unless Bid Sheet
            except Exception:
                continue
            return tmp, as_of, getattr(item, "Subject", "?"), recv
    return None, None, None, None


def main():
    force = "--force" in sys.argv
    allow_no_fut = "--allow-no-futures" in sys.argv
    if db._backend() == "sqlite":
        log.error("No shared backend (set USE_SNOWFLAKE + SNOWFLAKE_*) — refusing SQLite.")
        sys.exit(1)
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        log.error("pywin32 not installed — run: pip install pywin32")
        sys.exit(1)

    tmp, as_of, subject, received = _newest_bidsheet_xlsx()
    if not tmp:
        log.info("No Bid Sheet email found in the last %d from '%s' — nothing to do.",
                 MAX_SCAN, SENDER_FILTER)
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
        sys.exit(2)                               # flagged; the next run retries
    log.info("SAVED %s from '%s' (%s): %d CIF, %d freight, %d futures%s%s",
             res["as_of"], subject, received, res["n_cif"], res["n_frt"], res["n_fut"],
             "" if res["futures_complete"] else " [futures INCOMPLETE]",
             " [UPDATED existing]" if res["was_present"] else " [new]")


if __name__ == "__main__":
    main()
