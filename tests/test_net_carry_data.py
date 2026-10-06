"""The River FOB -> Net Carry adapter: how a sheet's months, contract codes, CBOT row and FOB values become the
net_carry module's `items` (delivery, futures symbol, basis in CENTS) and `curve` ({symbol: cents}).

    python tests/test_net_carry_data.py

Every expected number below is worked by hand from the sheet's own formula
    FOB = CIF - (tariff factor x freight%) / 2000 x bushel weight          (corn 56 lb, STL factor 3.99)
and from the Net Carry definitions (basis REF = quoted + futures(own) - futures(ref);
interest = ref price x rate x actual days / 360; net = basis REF - interest).
"""
import math
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fob_model as M          # noqa: E402
import net_carry as nc         # noqa: E402
import net_carry_data as nd    # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


def close(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


# ── the real 2026-10-05 corn sheet (8 months, Oct-May), as stored in the archive ──────────────
OCT05 = date(2026, 10, 5)
COLS_CORN = [("Oct", "CZ"), ("Nov", "CZ"), ("Dec", "CZ"), ("Jan", "CH"), ("Feb", "CH"), ("Mar", "CH"),
             ("Apr", "CK"), ("May", "CK")]
FUT_CORN = {"Oct": 4.975, "Nov": 4.975, "Dec": 4.975, "Jan": 5.1175, "Feb": 5.1175, "Mar": 5.1175,
            "Apr": 5.1875, "May": 5.1875}
CIF_CORN = {"Oct": 0.72, "Nov": 0.84, "Dec": 0.91, "Jan": 0.85, "Feb": 0.85, "Mar": 0.85, "Apr": 0.82, "May": 0.82}
FRT_STL = {"Oct": 7.0, "Nov": 6.0, "Dec": 5.75, "Jan": 5.5, "Feb": 5.25, "Mar": 5.0, "Apr": 4.5, "May": 4.5}

print("labels and months")
check("month_of: abbreviations, full names, 'Sept'",
      [nd.month_of(x) for x in ("Oct", "June", "July", "Sept", "December", "jan", " Feb ")] == [10, 6, 7, 9, 12, 1, 2])
check("month_of: Spot, blanks and numbers are not months",
      [nd.month_of(x) for x in ("Spot", "", None, "0.85", 7)] == [None, None, None, None, None])
check("is_spot", nd.is_spot("Spot") and nd.is_spot(" spot ") and not nd.is_spot("Oct") and not nd.is_spot(None))
check("delivery_label spells the year out", nd.delivery_label((2026, 10)) == "Oct 2026"
      and nd.delivery_label((2025, 3), spot=True) == "Spot Mar 2025")

print("contract code -> CME symbol with the right year")
sym = nd.contract_symbol
check("corn Oct 2026 + CZ = ZCZ26", sym("Corn", "CZ", (2026, 10)) == "ZCZ26")
check("corn Jan 2027 + CH = ZCH27 (next calendar year)", sym("Corn", "CH", (2027, 1)) == "ZCH27")
check("corn Dec 2026 + CH = ZCH27 (the contract comes AFTER the delivery, across the year end)",
      sym("Corn", "CH", (2026, 12)) == "ZCH27")
check("soybeans Dec 2026 + SF = ZSF27 (Jan contract, next year)", sym("Soybeans", "SF", (2026, 12)) == "ZSF27")
check("soybeans Nov 2026 + SX = ZSX26", sym("Soybeans", "SX", (2026, 11)) == "ZSX26")
check("wheat Oct 2026 + WZ = ZWZ26 (Chicago SRW root)", sym("Wheat", "WZ", (2026, 10)) == "ZWZ26")
check("a delivery quoted off the contract a month BEFORE it keeps that contract's year (Oct 2025 + CU -> ZCU25)",
      sym("Corn", "CU", (2025, 10)) == "ZCU25")
check("Jan 2026 quoted off last December (WZ) -> ZWZ25, not ZWZ26", sym("Wheat", "WZ", (2026, 1)) == "ZWZ25")
check("Feb 2024 + SF (the January contract) -> ZSF24", sym("Soybeans", "SF", (2024, 2)) == "ZSF24")
check("a 13th column a year out: Jun 2027 + CH -> ZCH27, July 2026 + CU -> ZCU26",
      sym("Corn", "CH", (2027, 6)) == "ZCH27" and sym("Corn", "CU", (2026, 7)) == "ZCU26")
check("unknown letter / blank code / unknown commodity -> None",
      sym("Corn", "CY", (2026, 10)) is None and sym("Corn", "", (2026, 10)) is None
      and sym("Corn", None, (2026, 10)) is None and sym("Oats", "OZ", (2026, 10)) is None)

print("placing the sheet's columns in time")
placed, notes, problem = nd.place_columns("Corn", OCT05, COLS_CORN)
check("a normal sheet places with no problem or notes", problem is None and not notes and len(placed) == 8)
check("deliveries run Oct 2026 .. May 2027 and the year rolls over after Dec",
      [c.ym for c in placed] == [(2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5)])
check("symbols carry the right years",
      [c.symbol for c in placed] == ["ZCZ26", "ZCZ26", "ZCZ26", "ZCH27", "ZCH27", "ZCH27", "ZCK27", "ZCK27"],
      [c.symbol for c in placed])
check("the label net_carry reads has an explicit year", [c.delivery for c in placed][:4]
      == ["Oct 2026", "Nov 2026", "Dec 2026", "Jan 2027"])

jun_cols = [("June", "CN"), ("July", "CN"), ("Aug", "CU"), ("Sep", "CU"), ("Oct", "CZ"), ("Nov", "CZ"), ("Dec", "CZ"),
            ("Jan", "CH")]
p2, _, pr2 = nd.place_columns("Corn", date(2026, 6, 24), jun_cols)
check("a June window (full month names 'June'/'July', the old contract cycle) places",
      pr2 is None and [c.ym for c in p2][0] == (2026, 6) and [c.ym for c in p2][-1] == (2027, 1)
      and [c.symbol for c in p2] == ["ZCN26", "ZCN26", "ZCU26", "ZCU26", "ZCZ26", "ZCZ26", "ZCZ26", "ZCH27"])

spot_cols = [("Spot", "CN"), ("Jun", "CN"), ("Jul", "CN"), ("Aug", "CU")]
p3, _, pr3 = nd.place_columns("Corn", date(2024, 6, 12), spot_cols)
check("a 'Spot' column is the prompt bid in the as-of month, ahead of that month's own column",
      pr3 is None and [(c.label, c.ym, c.spot) for c in p3][:2] == [("Spot", (2024, 6), True), ("Jun", (2024, 6), False)]
      and p3[0].delivery == "Spot Jun 2024")

p4, _, pr4 = nd.place_columns("Corn", date(2024, 7, 31), [("Spot", "CU"), ("Sep", "CU"), ("Oct", "CZ")])
check("a sheet that starts two months out ('Spot', then Sep, on Jul 31) is placed: Spot = July, Sep = Sep 2024",
      pr4 is None and [c.ym for c in p4] == [(2024, 7), (2024, 9), (2024, 10)])

p5, _, pr5 = nd.place_columns("Corn", date(2025, 12, 31),
                              [("Dec", "CH"), ("Jan", "CH"), ("Feb", "CH"), ("Mar", "CH")])
check("a December date rolls into January of the next year", pr5 is None and p5[1].ym == (2026, 1)
      and [c.symbol for c in p5] == ["ZCH26", "ZCH26", "ZCH26", "ZCH26"], [c.symbol for c in p5])

p6, n6, pr6 = nd.place_columns("Corn", date(2025, 3, 12), [("Spot", "CH"), ("Jan", "CH"), ("Feb", "CH"), ("Mar", "CH")])
check("stale month labels are refused, not guessed (Jan..Mar on a Mar 12 sheet)",
      p6 == [] and pr6 and "Jan" in pr6 and "Mar 12, 2025" in pr6 and "before" in pr6, pr6)
_, _, pr7 = nd.place_columns("Corn", date(2026, 1, 5), [("Jun", "CN"), ("Jul", "CN")])
check("labels that start far after the date are refused too", pr7 and "after" in pr7, pr7)
_, _, pr8 = nd.place_columns("Corn", OCT05, [])
check("no calendar saved -> a plain problem", pr8 and "no month / contract record" in pr8, pr8)
_, _, pr9 = nd.place_columns("Corn", OCT05, [("0.85", "CZ"), ("junk", "CZ")])
check("a sheet with no month label at all -> a plain problem", pr9 and "None of this sheet" in pr9, pr9)

p10, n10, _ = nd.place_columns("Corn", OCT05, [("Oct", "CZ"), ("0.85", "CZ"), ("Nov", "CZ")])
check("a junk label between months is ignored with a note, the rest still placed",
      [c.label for c in p10] == ["Oct", "Nov"] and any("0.85" in n for n in n10), (p10, n10))
p11, n11, _ = nd.place_columns("Corn", OCT05, [("Oct", "CZ"), ("Oct", "CZ"), ("Nov", "CZ")])
check("an immediately repeated month label is dropped with a note, not shifted a year",
      [c.ym for c in p11] == [(2026, 10), (2026, 11)] and any("repeated" in n for n in n11), (p11, n11))
p12, n12, _ = nd.place_columns("Corn", OCT05, [("Oct", "CZ"), ("Nov", "CY"), ("Dec", "")])
check("an unresolvable contract code keeps the column (symbol None) and says so",
      [c.symbol for c in p12] == ["ZCZ26", None, None] and len(n12) == 2, (p12, n12))
p13, _, pr13 = nd.place_columns("Corn", date(2026, 7, 20),
                                [(m, "CU") for m in ("July", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar",
                                                    "Apr", "May", "June")])
check("a 12-column window places June as June 2027", pr13 is None and p13[-1].ym == (2027, 6))

print("the futures curve (cents)")
curve, cn = nd.curve_from_row(placed, FUT_CORN)
check("one price per contract, in cents", curve == {"ZCZ26": 497.5, "ZCH27": 511.75, "ZCK27": 518.75}, curve)
check("no notes on a clean row", cn == [])
curve2, cn2 = nd.curve_from_row(placed, {"Oct": 4.975, "Nov": 5.0, "Jan": 5.1175})
check("the first price seen for a contract wins and a conflicting second one is noted",
      curve2.get("ZCZ26") == 497.5 and any("two different prices" in n for n in cn2), (curve2, cn2))
curve3, cn3 = nd.curve_from_row(placed, {"Oct": 25.0, "Jan": float("nan"), "Apr": None, "May": 5.1875})
check("an implausible price (25) is ignored with a note; NaN and None are blanks; May still prices ZCK27",
      curve3 == {"ZCK27": 518.75} and len(cn3) == 1 and "25" in cn3[0] and "$" not in cn3[0], (curve3, cn3))
check("no row -> empty curve", nd.curve_from_row(placed, None)[0] == {})
check("a column whose code can't be resolved can't price anything",
      nd.curve_from_row(p12, {"Oct": 4.975, "Nov": 5.0, "Dec": 5.0})[0] == {"ZCZ26": 497.5})

print("a whole sheet: problems are plain-language, never a guess")
sh = nd.build_sheet("Corn", OCT05, COLS_CORN, FUT_CORN)
check("a good sheet has no problem, 8 columns and a 3-contract curve",
      sh.problem is None and len(sh.columns) == 8 and len(sh.curve) == 3)
sh_nf = nd.build_sheet("Corn", date(2026, 6, 24), jun_cols, {})
check("no futures saved (2026-06-24 style) -> a problem that says so and does not blame the era",
      sh_nf.problem and "No futures prices were saved" in sh_nf.problem and "2023" not in sh_nf.problem, sh_nf.problem)
sh_old = nd.build_sheet("Corn", date(2023, 1, 4), [("Spot", "CH"), ("Jan", "CH"), ("Feb", "CH")], {})
check("before the archive kept futures, the message says when it began",
      sh_old.problem and "Feb 2023" in sh_old.problem, sh_old.problem)
sh_stale = nd.build_sheet("Corn", date(2025, 3, 12), [("Spot", "CH"), ("Jan", "CH")], {"Spot": 4.4})
check("a stale-labelled sheet comes back with the labels problem and nothing placed",
      sh_stale.problem and "labels" in sh_stale.problem and sh_stale.columns == [] and sh_stale.curve == {})
sh_live = nd.build_sheet("Corn", OCT05, COLS_CORN, {"Oct": None, "Nov": float("nan")}, live=True)
check("the working sheet with no futures says so in its own words (paste or pull on the Inputs tab)",
      sh_live.problem and "working sheet" in sh_live.problem and "Inputs tab" in sh_live.problem and "saved" not in sh_live.problem,
      sh_live.problem)
check("an unknown commodity is a problem, not an exception", nd.build_sheet("Oats", OCT05, COLS_CORN, FUT_CORN).problem)
check("a calendar of None is a problem", nd.build_sheet("Corn", OCT05, None, FUT_CORN).problem)

print("FOB -> items in cents (hand-worked)")
months = ["Oct", "Nov", "Dec", "Jan"]
grid = M.compute_fob_grid("Corn", CIF_CORN, {"STL": FRT_STL}, months)
fob = grid["STL"]
check("sheet FOB Oct = 0.72 - 3.99x7.0/2000x56 = -0.06204 $/bu", close(fob["Oct"], -0.06204), fob["Oct"])
check("sheet FOB Jan = 0.85 - 3.99x5.5/2000x56 = 0.23554 $/bu", close(fob["Jan"], 0.23554), fob["Jan"])
items = nd.items_for(sh, fob)
check("one item per month with an FOB value (the other 4 months of the window have no freight in this tiny grid)",
      [i["delivery"] for i in items] == ["Oct 2026", "Nov 2026", "Dec 2026", "Jan 2027"], items)
check("basis is the FOB in CENTS: -6.204 / 16.968 / 26.761 / 23.554",
      [round(i["basis"], 3) for i in items] == [-6.204, 16.968, 26.761, 23.554], [i["basis"] for i in items])
check("each item carries its month's contract", [i["futures"] for i in items] == ["ZCZ26"] * 3 + ["ZCH27"])
grid_gap = {"Oct": 0.5, "Nov": None, "Dec": float("nan"), "Jan": 0.4}
it_gap = nd.items_for(sh, grid_gap)
check("a month with no FOB (closed reach / blank CIF / NaN) is simply left out",
      [i["delivery"] for i in it_gap] == ["Oct 2026", "Jan 2027"] and [i["basis"] for i in it_gap] == [50.0, 40.0], it_gap)
check("no FOB row at all -> no items", nd.items_for(sh, None) == [] and nd.items_for(sh, {}) == [])
sp_items = nd.items_for(nd.build_sheet("Corn", date(2024, 6, 12), spot_cols,
                                       {"Spot": 4.0, "Jun": 4.0, "Jul": 4.0, "Aug": 3.9075}),
                        {"Spot": 0.29, "Jun": 0.29, "Jul": 0.32, "Aug": 0.17})
check("a Spot column becomes its own 'Spot Jun 2024' item beside 'Jun 2024'",
      [i["delivery"] for i in sp_items][:3] == ["Spot Jun 2024", "Jun 2024", "Jul 2024"])

print("through the vendored net_carry (hand-checked numbers)")
RATE = 0.0613
rows, meta = nc.compute_net_carry(items, "ZCZ26", sh.curve, 10, RATE)
check("reference ZCZ26 board = 497.5 cents", meta["ref_price"] == 497.5 and meta["anchor_ym"] == (2026, 10))
r_oct, r_nov, r_dec, r_jan = rows
check("Oct: the anchor month, no interest, net = quoted", r_oct.months == 0 and close(r_oct.net, -6.204))
days = (date(2027, 1, 1) - date(2026, 10, 1)).days
check("Jan 2027 is 92 days after Oct 1", days == 92)
check("Jan futures spread vs ZCZ26 = 511.75 - 497.5 = 14.25", close(r_jan.credit, 14.25), r_jan.credit)
check("Jan basis REF = 23.554 + 14.25 = 37.804", close(r_jan.basis_ref, 37.804), r_jan.basis_ref)
exp_int = 497.5 * RATE * 92 / 360
check(f"Jan interest = 497.5 x 6.13 pct x 92/360 = {exp_int:.4f}", close(r_jan.interest, exp_int), r_jan.interest)
check("Jan net = basis REF - interest", close(r_jan.net, 37.804 - exp_int), r_jan.net)
check("Jan carry = Dec net - Jan net", close(r_jan.carry, r_dec.net - r_jan.net))
check("Nov: same contract as the reference, so nothing to credit", close(r_nov.credit, 0.0))
check("every row converted (all contracts priced)", meta["all_converted"])

pts = nc.monthly_carry(rows)
top = nc.top_of_net_carry(pts, meta["anchor_ym"])
check("the top of net carry is the highest net from the anchor on",
      close(top["net"], max(r.net for r in rows if r.ym >= meta["anchor_ym"])) and top["label"] == "Jan 27", top)

print("reference choice on this sheet")
check("front mode = the contract the nearest delivery is quoted off",
      nc.reference_symbol("Corn", "front", sh.curve, OCT05, items=items, anchor_month=10) == "ZCZ26")
check("new-crop mode for corn = Dec (ZCZ26) and it is on this sheet",
      nc.reference_symbol("Corn", "newcrop", sh.curve, OCT05) == "ZCZ26" and "ZCZ26" in sh.curve)
check("new-crop wheat (ZWN27) is NOT on a wheat sheet that runs Oct-May - the app must fall back",
      nc.reference_symbol("Wheat", "newcrop", {"ZWZ26": 691.5}, OCT05) == "ZWN27")
check("without the front's price the module moves to the next priced delivery",
      nc.reference_symbol("Corn", "front", {"ZCH27": 511.75}, OCT05, items=items, anchor_month=10) == "ZCH27")

print("an unpriced contract degrades to raw basis, flagged")
partial = nd.build_sheet("Corn", OCT05, COLS_CORN, {"Oct": 4.975, "Nov": 4.975, "Dec": 4.975})
it_p = nd.items_for(partial, fob)
rows_p, meta_p = nc.compute_net_carry(it_p, "ZCZ26", partial.curve, 10, RATE)
check("Jan (ZCH27 unpriced) keeps its raw basis, is flagged, and the table says not all converted",
      not rows_p[3].converted and rows_p[3].credit is None and close(rows_p[3].basis_ref, 23.554)
      and not meta_p["all_converted"])

print("which locations to compare")
active = [it[1] for it in M.BLOCK_LAYOUT if it[0] == "fob"]
by_name = {l.name: l for l in M.LOCATIONS}
places = [(n, by_name[n].reach, by_name[n].factor) for n in active]
check("18 active river locations on the sheet", len(active) == 18, len(active))
check("Peoria's nearest on the Illinois River by tariff factor: Havana 4.64, Hennepin 5.07, Lacon 5.07 (tie -> sheet order)",
      nd.default_peers("Peoria", places, 3) == ["Havana", "Hennepin", "Lacon"], nd.default_peers("Peoria", places, 3))
check("Chicago (5.78) -> Seneca 5.24, Hennepin 5.07, Lacon 5.07",
      nd.default_peers("Chicago", places, 3) == ["Seneca", "Hennepin", "Lacon"], nd.default_peers("Chicago", places, 3))
stl = nd.default_peers("STL", places, 3)
check("STL is alone on its reach, so it is topped up with the closest factors (MTV 3.99, Cairo 3.80, Louisville 4.46)",
      stl == ["MTV", "Cairo", "Louisville"], stl)
check("a reach's own members come before the neighbours (Quincy -> Burlington, Davenport, Prairie du Chien)",
      nd.default_peers("Quincy", places, 3) == ["Burlington", "Davenport", "Prairie du Chien"],
      nd.default_peers("Quincy", places, 3))
check("never the location itself; n respected; unknown location -> []",
      "Peoria" not in nd.default_peers("Peoria", places, 5) and len(nd.default_peers("Peoria", places, 5)) == 5
      and nd.default_peers("Atlantis", places, 3) == [])

print("odd real sheets from the archive")
aug06 = nd.build_sheet("Corn", date(2025, 8, 6),
                       [("Spot", "CU"), ("Aug", "CU"), ("Sep", "CU"), ("Oct", "CU"), ("Nov", "CZ"), ("Dec", "CZ"),
                        ("Jan", "CH")],
                       {"Spot": 3.99, "Aug": 3.99, "Sep": 3.99, "Oct": 4.315, "Nov": 4.315, "Dec": 4.315, "Jan": 4.525})
check("2025-08-06 corn: Oct quoted off the Sept contract stays ZCU25 and nothing phantom is priced",
      [c.symbol for c in aug06.columns] == ["ZCU25"] * 4 + ["ZCZ25", "ZCZ25", "ZCH26"]
      and sorted(aug06.curve) == ["ZCH26", "ZCU25", "ZCZ25"], (aug06.curve, [c.symbol for c in aug06.columns]))
dec06 = nd.build_sheet("Corn", date(2023, 12, 6),
                       [("Spot", "CH"), ("Dec", "CH"), ("Jan", "CH"), ("Apr", "CK")],
                       {"Spot": 4.8425, "Dec": 4.8425, "Jan": 4.8425, "Apr": 4.9575})
check("2023-12-06 corn: December delivery quoted off March is next year's contract (ZCH24)",
      [c.symbol for c in dec06.columns] == ["ZCH24", "ZCH24", "ZCH24", "ZCK24"])
wz = nd.build_sheet("Wheat", date(2025, 12, 3), [("Jan", "WZ"), ("Feb", "WH"), ("Apr", "WK")],
                    {"Jan": 6.185, "Feb": 6.185, "Apr": 5.8425})
check("2025-12-03 wheat: January quoted off December (WZ) is ZWZ25", [c.symbol for c in wz.columns] == ["ZWZ25", "ZWH26", "ZWK26"])
soy = nd.build_sheet("Soybeans", date(2023, 8, 2), [("Spot", "SX"), ("Aug", "SX"), ("Dec", "SF"), ("Jan", "SF"), ("Feb", "SF")],
                     {"Spot": 13.2125, "Aug": 13.2125, "Dec": 13.3, "Jan": 13.3, "Feb": 13.3})
check("2023-08-02 soybeans: Dec/Jan/Feb all read ZSF24 (Feb is quoted off the Jan contract)",
      [c.symbol for c in soy.columns] == ["ZSX23", "ZSX23", "ZSF24", "ZSF24", "ZSF24"], [c.symbol for c in soy.columns])

print("net_carry_data: the futures history behind the Return to Carry section (the basis tracker's FUTURES_PRICES)")
import sqlite3                 # noqa: E402

db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE futures_prices (date TEXT NOT NULL, symbol TEXT NOT NULL, price_cents REAL, captured_at TEXT)")
db.executemany("INSERT INTO futures_prices (date, symbol, price_cents) VALUES (?,?,?)", [
    ("2026-10-02", "ZCZ26", 502.25), ("2026-10-02", "ZCH27", 516.75), ("2026-10-02", "ZSX26", 1284.0), ("2026-10-02", "ZCK27", None),
    ("2026-10-05", "ZCZ26", 510.0), ("2026-10-06", "ZSF27", 1301.5), ("2003-01-02", "ZCH03", 230.0)])
CALLS = []


def fake_rows(sql, params):
    """bids_data._sf_rows' contract: (sql with %s placeholders, params) -> [dict]."""
    CALLS.append((sql, params))
    cur = db.execute(sql.replace("%s", "?"), params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


fh = nd.futures_history("ZC", fake_rows)
check("one query, the root a bound LIKE parameter and the start date another (nothing pasted into the SQL)",
      len(CALLS) == 1 and CALLS[0][1] == ("ZC%", "2004-01-01") and "ZC" not in CALLS[0][0] and CALLS[0][0].count("%s") == 2, CALLS)
check("every stored corn contract of every day since 2004, as {date: {symbol: cents}}; a NULL price is skipped and soybeans are not in it",
      fh[date(2026, 10, 2)] == {"ZCZ26": 502.25, "ZCH27": 516.75} and fh[date(2026, 10, 5)] == {"ZCZ26": 510.0}
      and not any(s.startswith("ZS") for px in fh.values() for s in px) and date(2003, 1, 2) not in fh)
check("the analyst-sheet weeks before the settlement archive are underneath (corn from 1996), the stored settlements on top",
      min(fh) < date(2006, 1, 1) and all(s.startswith("ZC") for px in fh.values() for s in px))
fs = nd.futures_history("ZS", fake_rows)
check("soybeans: the sheet weeks from 2005 and the stored settlements, ZS contracts only",
      min(fs) < date(2007, 1, 1) and fs[date(2026, 10, 6)] == {"ZSF27": 1301.5} and fs[date(2026, 10, 2)] == {"ZSX26": 1284.0})
check("bids_data is not imported when a reader is handed in", "bids_data" not in sys.modules)

print("\n" + ("ALL PASS" if not FAILS else "FAILURES: %s" % FAILS))
sys.exit(1 if FAILS else 0)
