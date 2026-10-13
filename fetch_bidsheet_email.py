"""
fetch_bidsheet_email.py — INTERIM desktop fetcher.

Finds Doug Schultz's daily **Bid Sheet** emails in Outlook (via Outlook COM, like
vessel-lineup's fetcher), grabs each .xlsx attachment, and upserts its CIF +
barge-freight into the River FOB Snowflake archive via `bidsheet`.

This is the desktop stop-gap (no IT needed). The droplet/Graph version will
replace it once Graph `Mail.Read` is granted (see deploy/IT_REQUEST_mail_read.md);
both front-ends call the same `bidsheet.save_bidsheet()`.

Windows Task `RiverFobBidSheetImport` runs it every 10 minutes, 3:00-7:00 PM CT on
weekdays (Doug's sheet lands in the afternoon: 2:09-5:51 PM over 10/01-10/07), so a
sheet is archived within ~10 min of arriving, or at 3:00 if it came earlier:
    C:\\Python314\\python.exe "<repo>\\fetch_bidsheet_email.py"
Needs: pywin32 (Outlook COM), the repo .env (USE_SNOWFLAKE + SNOWFLAKE_*).

CATCH-UP (2026-10-12). On Fri 10/09 every run crashed before saving, and by Monday
the sheet was past the 120 newest mails this used to look at, so the day stayed
missing until loaded by hand. Now each run takes every Bid Sheet received since
the day after the last one it handled (LAST_DONE), going back at most
CATCHUP_DAYS, and saves the days that aren't archived yet, oldest first:
  - today's sheet takes the live CBOT board from Massive (between the 1:20 close
    and the 7 PM reopen, that's the close);
  - a past day's sheet, or today's after 7 PM, takes THAT day's settlement from
    Massive's daily session bars. The live board can't give it. The basis
    tracker's FUTURES_PRICES is a day off, so it isn't used (bidsheet._closing_board).
A day already archived (e.g. pasted by hand) is never overwritten unless --force.
LAST_DONE only moves past a day once it's handled, so a failed day is retried.
Most polls still exit early: once today is handled, or when no new sheet has come
in, a run neither scans far nor queries Snowflake.

LOG. Every run appends to logs/bidsheet_import.log (rotated; gitignored), with the
full traceback if it crashes. Task Scheduler keeps none of the output. Exit codes:
0 = done / nothing to do, 1 = crash, 2 = a day couldn't get its futures (the save
was refused; the next run retries).

    python fetch_bidsheet_email.py                          # what the task runs
    python fetch_bidsheet_email.py --dry-run                # find, parse, price; save nothing
    python fetch_bidsheet_email.py --since 2026-10-05       # look back to a date (skips archived days)
    python fetch_bidsheet_email.py --force                  # re-save the newest sheet even if archived
    python fetch_bidsheet_email.py --allow-no-futures       # save CIF/freight even with no board
"""
import os
import sys
import time
import tempfile
import logging
import logging.handlers
import datetime as dt

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
CATCHUP_DAYS = 10             # a missed sheet older than this is left to a person
MAX_SCAN = 5000               # safety cap on inbox items walked; the date cutoff ends the walk first
# The task polls every 10 min through the afternoon. This local note of the newest
# sheet date already handled lets most polls exit without a Snowflake query. Each
# query wakes the warehouse, which bills a minute minimum. Losing the file only
# costs one wider look back (CATCHUP_DAYS) and one Snowflake check.
LAST_DONE = os.path.join(tempfile.gettempdir(), "river_fob_bidsheet_last_done.txt")
LOG_PATH = os.path.join(HERE, "logs", "bidsheet_import.log")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bidsheet_fetch")
try:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    _fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=512_000,
                                               backupCount=3, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_fh)
except OSError:
    pass                      # no log file is never a reason to skip the import


def _inbox_items():
    """Classic Outlook's Inbox, newest first. Retries a few times: a COM call can
    fail while Outlook is starting or busy, and a COM error there is the likeliest
    reason every run crashed on Fri 10/09 (none of them reached Snowflake)."""
    import pywintypes
    import win32com.client
    for attempt in range(3):
        try:
            ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            items = ns.GetDefaultFolder(6).Items          # 6 = Inbox
            items.Sort("[ReceivedTime]", True)            # newest first
            return items
        except pywintypes.com_error as e:
            if attempt == 2:
                raise
            log.warning("Outlook didn't answer (%s); retrying in 20s.", e)
            time.sleep(20)


def _bidsheets_since(cutoff):
    """{as_of: (xlsx_path, subject, received)} for every Bid Sheet attachment from
    the sender received on or after `cutoff` (a date). When a date repeats, the
    newest email wins. Outlook's ReceivedTime is LOCAL time even though pywin32
    labels it +00:00, so its .date() is compared as-is, never converted."""
    who_match = SENDER_FILTER.lower()
    found = {}
    for n, item in enumerate(_inbox_items()):
        if n >= MAX_SCAN:
            break
        try:
            if item.Class != 43:                  # 43 = olMail
                continue
            recv = item.ReceivedTime
        except Exception:
            continue
        if recv.date() < cutoff:                  # newest first: the rest are older
            break
        # Match the sender in PYTHON — Outlook's DASL sender Restrict undercounts
        # (silently misses matching mail), so we scan and filter here instead.
        try:
            who = (str(item.SenderName or "") + " "
                   + str(item.SenderEmailAddress or "")).lower()
        except Exception:
            who = ""
        if who_match not in who:
            continue
        try:
            atts = item.Attachments
        except Exception:
            continue
        for att in atts:
            name = str(getattr(att, "FileName", ""))
            if not name.lower().endswith(".xlsx"):
                continue
            # The date comes from the FILENAME (MMDDYY), not the sheet's A1 cell
            # (A1 has been a day behind the real sheet). Email date as a fallback.
            as_of = bidsheet.date_from_filename(name) or recv.date()
            if as_of in found:                    # a newer email already gave this date
                continue
            tmp = os.path.join(tempfile.gettempdir(), f"bidsheet_fetch_{as_of:%Y%m%d}.xlsx")
            try:
                att.SaveAsFile(tmp)
                bidsheet.parse_bidsheet(tmp, as_of=as_of)   # raises unless Bid Sheet
            except Exception:
                continue
            found[as_of] = (tmp, getattr(item, "Subject", "?"), recv)
    return found


def _futures_source(as_of, now):
    """Which CBOT board a sheet takes. A past day: that day's settlement from
    Massive's daily bars ("close"). Today: the live Massive board, which between
    the 1:20 PM close and the 7 PM reopen IS the close. After the reopen the live
    board is overnight trade, so a late run (laptop back after 7, a catch-up at
    log-on) takes today's settlement too."""
    return "close" if (as_of < now.date() or now.hour >= 19) else "live"


def _last_done():
    try:
        return dt.date.fromisoformat(open(LAST_DONE).read().strip())
    except Exception:
        return None


def _mark_done(as_of):
    """Record `as_of` as handled. Only ever moves forward, so a --since look back
    at older days can't reopen days already handled."""
    current = _last_done()
    if current is not None and as_of <= current:
        return
    try:
        with open(LAST_DONE, "w") as f:
            f.write(as_of.isoformat())
    except OSError:
        pass


def main():
    args = sys.argv[1:]
    force = "--force" in args
    dry = "--dry-run" in args
    allow_no_fut = "--allow-no-futures" in args
    since = (dt.date.fromisoformat(args[args.index("--since") + 1])
             if "--since" in args else None)
    now = dt.datetime.now()                       # the desktop runs on Central time
    today = now.date()
    done = None if (force or since) else _last_done()
    if done is not None and done >= today:
        log.info("Today's Bid Sheet (%s) is already archived; nothing to do.", done)
        return 0
    if db._backend() == "sqlite":
        log.error("No shared backend (set USE_SNOWFLAKE + SNOWFLAKE_*) — refusing SQLite.")
        return 1
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        log.error("pywin32 not installed — run: pip install pywin32")
        return 1

    floor = today - dt.timedelta(days=CATCHUP_DAYS)
    if since:
        cutoff = since
    elif done:
        cutoff = max(floor, done + dt.timedelta(days=1))
    else:
        cutoff = floor
    sheets = _bidsheets_since(cutoff)
    if force and not since:
        # --force alone keeps its old meaning: re-save the newest sheet only, never a
        # sweep of past days that might hold hand corrections.
        pending = [max(sheets)] if sheets else []
    else:
        pending = sorted(d for d in sheets if done is None or d > done)
    if not pending:
        log.info("No new Bid Sheet since %s (newest found: %s); nothing to do.",
                 cutoff, max(sheets) if sheets else "none")
        return 0

    archived = {str(d)[:10] for d in db.list_dates()}
    rc, advance = 0, not dry
    for as_of in pending:
        tmp, subject, received = sheets[as_of]
        # pywin32 labels Outlook times +00:00 but they're LOCAL; print them plainly.
        received = received.strftime("%a %m-%d %H:%M") if received else "?"
        if as_of.isoformat() in archived and not force:
            log.info("%s (email '%s', %s) is already archived; skipping. "
                     "Use --force to re-save.", as_of, subject, received)
        else:
            source = _futures_source(as_of, now)
            try:
                res = bidsheet.save_bidsheet(tmp, as_of=as_of, commit=not dry,
                                             require_futures=not allow_no_fut,
                                             futures=source)
            except bidsheet.FuturesUnavailable as e:
                log.error("%s", e)
                rc, advance = 2, False            # retried on the next run
                continue
            log.info("%s %s from '%s' (%s): %d CIF, %d freight, %d futures "
                     "(%s)%s%s", "DRY RUN, would save" if dry else "SAVED",
                     res["as_of"], subject, received, res["n_cif"], res["n_frt"],
                     res["n_fut"], "today's live board" if source == "live"
                     else "that day's settlement",
                     "" if res["futures_complete"] else " [futures INCOMPLETE]",
                     " [UPDATED existing]" if res["was_present"] else " [new]")
        if advance:
            _mark_done(as_of)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        log.exception("Bid Sheet import crashed")
        sys.exit(1)
