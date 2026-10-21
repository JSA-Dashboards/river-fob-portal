"""The 📝 Inputs tab's paste parser: the Ceres bid/ask barge freight table (2026-10-21) and the older single-header table.

    python tests/test_paste_parse.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import paste_parse as pp      # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


# Kolten's paste of 2026-10-21, as Excel/Outlook copies it: tab-separated, a tab-only spacer line between rows, blank cells kept.
CERES = (
    "Ship \tSt. Paul\tMcGregor\tDavenport\tSt. Louis\tSt. Louis\tIllinois\tOhio\tOhio\tMTCT\n"
    "Period\tSavage\tSouth\tSouth\t14'\t12'\t\tJeff/Cincy\tLower\t\n"
    "WK 10/18\t775/825\t750/800\t750/800\t575/625\t600/650\t750/800\t675/725\t675/725\t475/500\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "WK 10/25\t750/800\t725/775\t725/775\t575/625\t600/650\t725/775\t650/700\t650/700\t450/475\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "NOV\t725/775\t700/750\t700/750\t550/600\t575/625\t675/725\t625/675\t625/675\t425/475\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "DEC\t\t675/725\t675/725\t525/575\t550/600\t650/675\t575/625\t575/625\t425/475\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "JAN\t\t\t\t525/575\t550/600\t650/675\t575/625\t575/625\t425/475\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "FEB\t\t\t\t500/550\t525/575\t600/650\t550/600\t550/600\t425/475\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "MAR\t\t\t\t475/525\t500/550\t575/625\t525/575\t525/575\t425/475\n"
    "\t\t\t\t\t\t\t\t\t\n"
    "AMJJ\t600/650\t575/625\t575/625\t450/500\t475/525\t550/600\t500/550\t500/550\t425/450\n"
)

print("the Ceres bid/ask table")
res, err = pp.parse_freight(CERES)
f = res["freight"] if res else {}
check("it is read as the Ceres table, with no error", err is None and res["format"] == "ceres", err)
check("seven reaches: St. Paul/Savage = Upper Miss, McGregor, Davenport, St. Louis 14' = STL, Illinois = IL, Ohio Jeff/Cincy = Ohio, MTCT = Lower Miss",
      set(f) == {"Upper Miss", "McGregor South", "Davenport South", "STL", "IL", "Ohio", "Lower Miss"}
      and res["used"]["STL"] == "St. Louis 14'" and res["used"]["Ohio"] == "Ohio Jeff/Cincy" and res["used"]["Upper Miss"] == "St. Paul Savage", res["used"])
check("the BID of each pair, as a tariff multiple (775/825 -> 7.75)", f["Upper Miss"]["Oct"] == 7.75 and f["Lower Miss"]["Nov"] == 4.25)
check("October = the first week (WK 10/18), not WK 10/25: IL 7.50, McGregor 7.50, MTCT 4.75",
      f["IL"]["Oct"] == 7.5 and f["McGregor South"]["Oct"] == 7.5 and f["Lower Miss"]["Oct"] == 4.75)
check("St. Louis uses the 14' column (Oct 5.75, Nov 5.50 ... — what Doug's Bid Sheet shows), never 12' (6.00)",
      [f["STL"][m] for m in ("Oct", "Nov", "Dec", "Jan", "Feb", "Mar")] == [5.75, 5.5, 5.25, 5.25, 5.0, 4.75])
check("Ohio is Jeff/Cincy (Nov 6.25), Ohio Lower is not used", f["Ohio"]["Nov"] == 6.25 and "Ohio Lower" not in res["used"].values())
check("AMJJ fills April, May, June and July", all(f["STL"][m] == 4.5 for m in ("Apr", "May", "June", "July")) and f["Lower Miss"]["Apr"] == 4.25)
check("a blank cell comes back as None (closed / not quoted): St. Paul Dec-Mar, McGregor and Davenport Jan-Mar",
      all(f["Upper Miss"][m] is None for m in ("Dec", "Jan", "Feb", "Mar")) and all(f["McGregor South"][m] is None for m in ("Jan", "Feb", "Mar"))
      and f["McGregor South"]["Dec"] == 6.75)
check("...while St. Paul's AMJJ quote still fills its spring months (6.00)", f["Upper Miss"]["Apr"] == 6.0 and f["Upper Miss"]["July"] == 6.0)
check("IL's Dec/Jan quote 650/675 reads 6.50", f["IL"]["Dec"] == 6.5 and f["IL"]["Jan"] == 6.5)
check("no date in the table (WK 10/18 has no year): none is guessed", res["date"] is None)

print("ranks and periods")
check("a month row beats a week row in the same month, whichever comes first",
      pp.parse_freight("x\tSt. Paul\tIllinois\tMTCT\ny\tSavage\t\t\nNOV\t700/750\t600/650\t400/450\nWK 11/8\t800/850\t650/700\t450/500\n")[0]["freight"]["IL"]["Nov"] == 6.0)
check("a package only fills months with no row of their own (JFM after a JAN row: Jan stays the month's, Feb/Mar from the package)",
      pp.parse_freight("x\tSt. Paul\tIllinois\tMTCT\ny\tSavage\t\t\nJAN\t\t500/550\t400\nJFM\t\t450/500\t380\n")[0]["freight"]["IL"] == {"Jan": 5.0, "Feb": 4.5, "Mar": 4.5})
check("period labels: 'WK 10/18' -> Oct, 'Week 11/1' -> Nov, AMJJ, JFM, NOV; 'TW' / 'Ship' are not periods",
      pp._period_months("WK 10/18") == (["Oct"], 2) and pp._period_months("Week 11/1") == (["Nov"], 2)
      and pp._period_months("AMJJ") == (["Apr", "May", "June", "July"], 1) and pp._period_months("JFM") == (["Jan", "Feb", "Mar"], 1)
      and pp._period_months("NOV") == (["Nov"], 3) and pp._period_months("TW") == ([], 0) and pp._period_months("Ship") == ([], 0))
check("a single number (no ask) reads as the bid too", pp._bid("575") == 5.75 and pp._bid("575/625") == 5.75 and pp._bid("") is None)
for name, lost in (("single spaces", CERES.replace("\t", " ")), ("wide spaces", CERES.replace("\t", "    "))):
    r_, e_ = pp.parse_freight(lost)
    check(f"a copy that LOST its tabs ({name}) is refused with a plain message, never read with shifted columns",
          r_ is None and "without its column breaks (tabs)" in e_ and "Nothing was filled" in e_, (r_ and r_.get("freight", {}).get("Upper Miss"), e_))

print("the older single-header table still reads as before")
OLD = ("Barge Freight 10/16/26\nMonth\tILL\tOHIO\tL OHIO\tMM\tCITIES\tSTL\tMTCT\nTW\t750\t725\t725\t775\t800\t600\t500\n"
       "OCT\t750%\t725%\t725%\t775%\t800%\t600%\t500%\nNOV\t675\t650\t650\t725\t750\t550\t475\n")
o, oe = pp.parse_freight(OLD)
check("ILL/OHIO/MM/CITIES/STL/MTCT -> the same regions, TW skipped, the table's date found",
      oe is None and o.get("format") is None and o["freight"]["IL"] == {"Oct": 7.5, "Nov": 6.75} and o["freight"]["Davenport South"]["Oct"] == 7.75
      and o["freight"]["McGregor South"]["Nov"] == 7.25 and o["freight"]["Lower Miss"]["Oct"] == 5.0 and str(o["date"]) == "2026-10-16", o)
check("nothing recognisable is still an error", pp.parse_freight("hello\nworld")[0] is None)

print("\n" + ("ALL PASS" if not FAILS else "FAILURES: %s" % FAILS))
sys.exit(1 if FAILS else 0)
