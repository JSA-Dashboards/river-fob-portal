"""The Bid Sheet import's catch-up (fetch_bidsheet_email) and the past-day close pricing
(bidsheet). Pure: a fake inbox, a fake archive and a recording save; no Outlook, no
Snowflake. Written after Fri 2026-10-09's sheet was missed and had to be loaded by hand.

    python tests/test_bidsheet_catchup.py
"""
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import logging.handlers              # noqa: E402
import bidsheet                      # noqa: E402
import fetch_bidsheet_email as F     # noqa: E402

# Keep this test's fake runs out of the real logs/bidsheet_import.log.
F.log.handlers = [h for h in F.log.handlers if not isinstance(h, logging.handlers.RotatingFileHandler)]
FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


D = dt.date

print("bidsheet: naming the contract a sheet code meant on a date")
check("Oct 9 2026: CZ is Dec 26, CH is Mar 27, SX is Nov 26, SF is Jan 27, WK is May 27",
      [bidsheet.close_symbol(c, k, D(2026, 10, 9)) for c, k in
       (("Corn", "CZ"), ("Corn", "CH"), ("Soybeans", "SX"), ("Soybeans", "SF"), ("Wheat", "WK"))]
      == ["ZCZ26", "ZCH27", "ZSX26", "ZSF27", "ZWK27"])
check("the as-of month itself counts as on/after (Dec 3: CZ is still Dec 26); Mar 10: CH is Mar 26",
      bidsheet.close_symbol("Corn", "CZ", D(2026, 12, 3)) == "ZCZ26"
      and bidsheet.close_symbol("Corn", "CH", D(2026, 3, 10)) == "ZCH26")

CAL = {"Corn": [("Oct", "CZ"), ("Nov", "CZ"), ("Dec", "CZ"), ("Jan", "CH"), ("Feb", "CH"), ("Mar", "CH"), ("Apr", "CK"), ("May", "CK")],
       "Soybeans": [("Oct", "SX"), ("Nov", "SX"), ("Dec", "SF"), ("Jan", "SF")]}
CLOSES = {"ZCZ26": 5.0025, "ZCH27": 5.1525, "ZCK27": 5.2225, "ZSX26": 12.875, "ZSF27": 13.0425}
fut = bidsheet.futures_from_closes(CAL, CLOSES, D(2026, 10, 9))
check("every month takes its contract's close (Fri 10/09's real values)",
      fut["Corn"] == {"Oct": 5.0025, "Nov": 5.0025, "Dec": 5.0025, "Jan": 5.1525, "Feb": 5.1525, "Mar": 5.1525,
                      "Apr": 5.2225, "May": 5.2225} and fut["Soybeans"]["Dec"] == 13.0425, fut)
gap = bidsheet.futures_from_closes(CAL, {k: v for k, v in CLOSES.items() if k != "ZCK27"}, D(2026, 10, 9))
check("a contract with no close leaves its months out instead of guessing",
      "Apr" not in gap["Corn"] and "May" not in gap["Corn"] and gap["Corn"]["Mar"] == 5.1525)
check("the spreads come from the same board, front minus next (CZ/CH -15.0)",
      round(dict(bidsheet._spreads_for(CAL, fut)["Corn"])["CZ/CH"], 4) == -0.15,
      bidsheet._spreads_for(CAL, fut)["Corn"])


# ---- the inbox walk ---------------------------------------------------------------
class Att:
    def __init__(self, name, body=b"bid sheet"):
        self.FileName, self.body = name, body

    def SaveAsFile(self, path):
        with open(path, "wb") as f:
            f.write(self.body)


class Mail:
    Class = 43

    def __init__(self, when, sender, subject, atts):
        self.ReceivedTime, self.SenderName, self.SenderEmailAddress = when, sender, ""
        self.Subject, self.Attachments = subject, atts


def fake_parse(path, as_of=None):
    if open(path, "rb").read() != b"bid sheet":
        raise ValueError("No 'Bid Sheet' tab")
    return as_of, {}, {}, {}


T = dt.datetime
INBOX = [  # newest first, as Outlook sorts it
    Mail(T(2026, 10, 12, 14, 30), "Doug Schultz", "FW: Monday, 10-12-2026", [Att("101226.xlsx")]),
    Mail(T(2026, 10, 12, 9, 0), "Someone Else", "lineups", [Att("101226.xlsx")]),
    Mail(T(2026, 10, 9, 15, 0), "Doug Schultz", "FW: Friday, 10-09-2026 (corrected)", [Att("100926.xlsx")]),
    Mail(T(2026, 10, 9, 14, 6), "Doug Schultz", "FW: Friday, 10-09-2026", [Att("100926.xlsx")]),
    Mail(T(2026, 10, 9, 13, 0), "Doug Schultz", "notes", [Att("notes.xlsx", b"no tab"), Att("memo.pdf")]),
    Mail(T(2026, 10, 8, 16, 9), "Doug Schultz", "FW: Thursday, 10-08-2026", [Att("100826.xlsx")]),
]
REAL_INBOX_ITEMS = F._inbox_items          # kept for the Outlook-refusal check below
F._inbox_items = lambda: iter(INBOX)
bidsheet.parse_bidsheet = fake_parse

print("\nfetch_bidsheet_email: which sheets a run sees")
got = F._bidsheets_since(D(2026, 10, 9))
check("only Doug's Bid Sheets from the cutoff on; Thursday's is before it, other senders and non-sheets are skipped",
      sorted(got) == [D(2026, 10, 9), D(2026, 10, 12)], sorted(got))
check("when a date repeats, the newest email wins (Friday's 3:00 correction over the 2:06 sheet)",
      got[D(2026, 10, 9)][1] == "FW: Friday, 10-09-2026 (corrected)", got[D(2026, 10, 9)][1])
check("a wider cutoff reaches Thursday too", D(2026, 10, 8) in F._bidsheets_since(D(2026, 10, 1)))

print("\nfetch_bidsheet_email: which board a sheet takes")
MON = D(2026, 10, 12)
check("a past day takes that day's captured close, whatever the hour",
      F._futures_source(D(2026, 10, 9), dt.datetime(2026, 10, 12, 15, 30)) == "close")
check("today, between the 1:20 close and the 7 PM reopen, takes the live board (which is the close)",
      F._futures_source(MON, dt.datetime(2026, 10, 12, 13, 45)) == "live"
      and F._futures_source(MON, dt.datetime(2026, 10, 12, 18, 59)) == "live")
check("today, after the 7 PM reopen, takes the captured close (the live board is overnight trade by then)",
      F._futures_source(MON, dt.datetime(2026, 10, 12, 19, 0)) == "close"
      and F._futures_source(MON, dt.datetime(2026, 10, 12, 20, 40)) == "close")

# ---- a run, end to end, with a fake archive -----------------------------------------
# The hour rule is checked just above; here the board is chosen by date alone, so these
# checks give the same answer whatever time of day they run.
F._futures_source = lambda as_of, now: "close" if as_of < now.date() else "live"
TODAY = dt.date.today()
DAYS = {"thu": TODAY - dt.timedelta(days=4), "fri": TODAY - dt.timedelta(days=3), "today": TODAY}
SAVES, MARK, SYNCS = [], {"v": None}, []


def run(argv, sheets, archived, done, fail_on=(), first_look=None, sync_ok=True):
    """`first_look` = what classic Outlook shows before a Send/Receive (None = already everything)."""
    SAVES.clear()
    SYNCS.clear()
    MARK["v"] = done
    looks = []

    def since(cutoff):
        shown = sheets if (first_look is None or looks) else first_look
        looks.append(cutoff)
        return {d: v for d, v in shown.items() if d >= cutoff}
    F._bidsheets_since = since
    F._sync_outlook = lambda: SYNCS.append(1) or sync_ok
    F._last_done = lambda: MARK["v"]
    F._mark_done = lambda d: MARK.__setitem__("v", d) if (MARK["v"] is None or d > MARK["v"]) else None
    F.db._backend = lambda: "snowflake"
    F.db.list_dates = lambda: [d.isoformat() for d in archived]

    def save(path, as_of=None, commit=False, require_futures=True, futures="live"):
        if as_of in fail_on:
            raise bidsheet.FuturesUnavailable("no close for %s" % as_of)
        SAVES.append((as_of, futures, commit))
        return dict(as_of=as_of.isoformat(), was_present=as_of in archived, n_cif=19, n_frt=48, n_fut=24,
                    futures_complete=True, futures_source=futures)
    F.bidsheet.save_bidsheet = save
    sys.argv = ["fetch_bidsheet_email.py"] + argv
    return F.main()


S = {d: ("x.xlsx", "FW: sheet", None) for d in DAYS.values()}
print("\nfetch_bidsheet_email: a run")
rc = run([], S, archived=[DAYS["thu"]], done=DAYS["thu"])
check("a missed day is caught up with THAT day's close, then today with the live board, oldest first",
      rc == 0 and SAVES == [(DAYS["fri"], "close", True), (DAYS["today"], "live", True)], SAVES)
check("the done note moves to today", MARK["v"] == DAYS["today"], MARK["v"])

rc = run([], S, archived=[DAYS["thu"], DAYS["fri"]], done=DAYS["thu"])
check("a day already archived (pasted by hand) is skipped, never overwritten",
      rc == 0 and SAVES == [(DAYS["today"], "live", True)], SAVES)

rc = run([], S, archived=[DAYS["thu"]], done=DAYS["thu"], fail_on=(DAYS["fri"],))
check("a past day with no close is refused (exit 2) but today still saves",
      rc == 2 and SAVES == [(DAYS["today"], "live", True)], (rc, SAVES))
check("and the done note stays before the failed day, so the next run retries it",
      MARK["v"] == DAYS["thu"], MARK["v"])

rc = run([], S, archived=[], done=DAYS["today"])
check("once today is done, a poll exits at once (nothing saved)", rc == 0 and SAVES == [])

rc = run(["--force"], S, archived=list(DAYS.values()), done=DAYS["today"])
check("--force alone re-saves only the newest sheet, not a sweep of past days",
      rc == 0 and SAVES == [(DAYS["today"], "live", True)], SAVES)

rc = run(["--dry-run"], S, archived=[DAYS["thu"]], done=DAYS["thu"])
check("--dry-run prices both days but commits nothing and leaves the done note alone",
      rc == 0 and [s[2] for s in SAVES] == [False, False] and MARK["v"] == DAYS["thu"], (SAVES, MARK["v"]))
check("a run that finds its sheets at once never asks Outlook to Send/Receive", SYNCS == [], SYNCS)

print("\nfetch_bidsheet_email: Outlook's offline copy is stale (11/04: the 2:00 PM sheet unseen at 2:20)")
stale = {DAYS["thu"]: S[DAYS["thu"]]}
rc = run([], S, archived=[DAYS["thu"]], done=DAYS["thu"], first_look=stale)
check("nothing new at first look -> one Send/Receive, a second look, and both days are saved",
      rc == 0 and SYNCS == [1] and SAVES == [(DAYS["fri"], "close", True), (DAYS["today"], "live", True)], (SYNCS, SAVES))
rc = run([], stale, archived=[DAYS["thu"]], done=DAYS["thu"])
check("still nothing after the Send/Receive -> nothing saved, done note unchanged",
      rc == 0 and SYNCS == [1] and SAVES == [] and MARK["v"] == DAYS["thu"], (SYNCS, SAVES, MARK["v"]))
rc = run([], S, archived=[DAYS["thu"]], done=DAYS["thu"], first_look=stale, sync_ok=False)
check("Outlook can't be asked -> no second look (the run saves nothing and exits 0)",
      rc == 0 and SYNCS == [1] and SAVES == [], (SYNCS, SAVES))
rc = run([], S, archived=[], done=DAYS["today"], first_look={})
check("once today is done, a poll still exits at once: no Send/Receive", rc == 0 and SYNCS == [], SYNCS)

print("\nfetch_bidsheet_email: waiting for the Inbox to settle (fake clock, 3 s steps)")


def settle(script, **kw):
    """`script` = the (signature, connected) the probe returns at each poll; the last one repeats."""
    t = {"now": 0.0}
    seq = iter(script)
    last = {"v": None}

    def probe():
        last["v"] = next(seq, last["v"])
        return last["v"]
    return F.wait_until_settled(probe, clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s), **kw)


ok, secs = settle([((10, "a"), True)])
check("an Inbox that never changes, connected: settled at the 20 s floor, not sooner", ok and secs == 21, (ok, secs))
ok, secs = settle([((10, "a"), True)] * 4 + [((11, "b"), True)])
check("new mail at 12 s restarts the 15 s settle clock (settled at 27 s)", ok and secs == 27, (ok, secs))
ok, secs = settle([((10, "a"), False)] * 30 + [((10, "a"), True)])
check("offline / disconnected never counts as settled; it settles 15 s after Outlook connects",
      ok and secs == 105, (ok, secs))
ok, secs = settle([((10, "a"), False)])
check("never connects -> gives up at the 120 s limit", (not ok) and secs == 120, (ok, secs))

print("\nfetch_bidsheet_email: Outlook refuses the import (11/10: stuck on Choose Profile 12:13-4:30 PM)")
import tempfile                      # noqa: E402
import pywintypes                    # noqa: E402
import win32com.client               # noqa: E402

real_dispatch, real_sleep = win32com.client.Dispatch, F.time.sleep
tries = []
win32com.client.Dispatch = lambda name: (tries.append(1), (_ for _ in ()).throw(
    pywintypes.com_error(-2147418111, "Call was rejected by callee.", None, None)))[1]
F.time.sleep = lambda s: None
try:
    REAL_INBOX_ITEMS()
    raised = None
except F.OutlookUnavailable as e:
    raised = e
finally:
    win32com.client.Dispatch, F.time.sleep = real_dispatch, real_sleep
check("three refusals -> OutlookUnavailable, carrying Outlook's message", raised is not None and len(tries) == 3
      and "rejected by callee" in str(raised), (raised, len(tries)))

TL = ('"Image Name","PID","Session Name","Session#","Mem Usage","Status","User Name","CPU Time","Window Title"\n'
      '"OUTLOOK.EXE","39180","Console","1","42,000 K","Running","AzureAD\\\\K","0:00:01","Choose Profile"\n'
      '"OUTLOOK.EXE","40000","Console","1","9,000 K","Unknown","AzureAD\\\\K","0:00:00","N/A"\n')
check("tasklist: the open window's title, not N/A or the header", F.outlook_windows(TL) == ["Choose Profile"], F.outlook_windows(TL))
check("tasklist: no Outlook running -> []", F.outlook_windows("INFO: No tasks are running which match the specified criteria.\n") == [])

NOW = dt.datetime(2026, 11, 10, 15, 9)
s, h = F.compose_outlook_alert(NOW, "Call was rejected by callee.", ["Choose Profile"], "2026-11-09")
check("Choose Profile -> says to pick Outlook and click OK", "Choose Profile" in h and "<b>Outlook</b>" in h and "OK" in h, h)
check("subject names the day; the body the time and the newest sheet",
      s == "[River FOB] Outlook isn't answering the Bid Sheet import (Tue Nov 10)" and "3:09 PM" in h and "2026-11-09" in h, s)
_, h2 = F.compose_outlook_alert(NOW, "x", ["Some <dialog>"], "2026-11-09")
check("another window is named (escaped)", "Some &lt;dialog&gt;" in h2 and "Choose Profile" not in h2, h2)
_, h3 = F.compose_outlook_alert(NOW, "x", [], "2026-11-09")
check("no window -> how to close classic Outlook", "Task Manager" in h3, h3)

tmpdir = tempfile.mkdtemp()
real_note, real_windows = F.OUTLOOK_ALERT, F.outlook_windows
F.OUTLOOK_ALERT = os.path.join(tmpdir, "alert.txt")
F.outlook_windows = lambda: ["Choose Profile"]
F.db.list_dates = lambda: ["2026-11-09", "2026-11-06"]
sent = []
send = lambda subj, html: sent.append(subj)
try:
    a1 = F.alert_outlook("refused", now=NOW, send=send)
    a2 = F.alert_outlook("refused", now=NOW + dt.timedelta(minutes=10), send=send)
    a3 = F.alert_outlook("refused", now=NOW + dt.timedelta(days=1), send=send)
    check("emails at the first refusal, not again the same day, again the next day",
          (a1, a2, a3) == (True, False, True) and len(sent) == 2, (a1, a2, a3, sent))

    def fail_send(subj, html):
        raise RuntimeError("Graph down")
    os.remove(F.OUTLOOK_ALERT)
    try:
        F.alert_outlook("refused", now=NOW, send=fail_send)
    except RuntimeError:
        pass
    check("a failed send isn't counted, so the next run tries again", F.alert_due(NOW.date()))

    calls = []
    real_main, real_alert = F.main, F.alert_outlook

    def refuse():
        raise F.OutlookUnavailable("rejected")
    F.main, F.alert_outlook = refuse, (lambda e: calls.append(str(e)))
    for argv, want in ((["fetch_bidsheet_email.py"], ["rejected"]), (["fetch_bidsheet_email.py", "--dry-run"], [])):
        calls.clear()
        sys.argv = argv
        try:
            F.run_cli()
            code = None
        except SystemExit as e:
            code = e.code
        check(f"the task run ({' '.join(argv[1:]) or 'scheduled'}) exits 1 and {'emails' if want else 'sends nothing'}",
              code == 1 and calls == want, (code, calls))
    F.main, F.alert_outlook = real_main, real_alert
finally:
    F.OUTLOOK_ALERT, F.outlook_windows = real_note, real_windows

print("\n" + ("ALL PASS" if not FAILS else "FAILURES: %s" % FAILS))
sys.exit(1 if FAILS else 0)
