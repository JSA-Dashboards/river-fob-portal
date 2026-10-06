"""The Net Carry tab's HTML (net_carry_view): the table, the amber top-of-net-carry marking, the summary line and
callout - drawn from the same numbers the vendored net_carry module computes, so these tests check the DRAWING:
the tagged row is the maximum net, colours follow the sign, and the markup is safe for Streamlit's markdown.

    python tests/test_net_carry_view.py
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import net_carry as nc            # noqa: E402
import net_carry_compare as ncmp  # noqa: E402
import net_carry_data as nd       # noqa: E402
import net_carry_view as nv       # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


def strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


# ── the real 2026-10-05 corn sheet at STL (same inputs as test_net_carry_data) ───────────────
OCT05 = date(2026, 10, 5)
COLS = [("Oct", "CZ"), ("Nov", "CZ"), ("Dec", "CZ"), ("Jan", "CH"), ("Feb", "CH"), ("Mar", "CH"), ("Apr", "CK"), ("May", "CK")]
FUT = {"Oct": 4.975, "Nov": 4.975, "Dec": 4.975, "Jan": 5.1175, "Feb": 5.1175, "Mar": 5.1175, "Apr": 5.1875, "May": 5.1875}
FOB_STL = {"Oct": -0.06204, "Nov": 0.16968, "Dec": 0.26761, "Jan": 0.23554, "Feb": 0.26348, "Mar": 0.29142,
           "Apr": 0.31736, "May": 0.31736}
RATE = 0.0613
sheet = nd.build_sheet("Corn", OCT05, COLS, FUT)
items = nd.items_for(sheet, FOB_STL)
rows, meta = nc.compute_net_carry(items, "ZCZ26", sheet.curve, 10, RATE)
pts = nc.monthly_carry(rows)
top = nc.top_of_net_carry(pts, meta["anchor_ym"])
html = nv.carry_table_html(rows, top["row"], "ZCZ26", "Corn · FOB Barge STL · Oct 05, 2026", banner=("#f4b41a", "#e09600"))


def parse(html_):
    body = re.findall(r"<tbody>(.*?)</tbody>", html_, flags=re.S)[0]
    out = []
    for tr in [t for t in body.split("</tr>") if "<td" in t]:
        cls = re.search(r'<tr class="([^"]*)"', tr).group(1)
        out.append((cls, [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)], tr))
    return out


print("number formats")
check("fmt: signed one decimal, em dash for None", nv.fmt(12.34) == "+12.3" and nv.fmt(-6.204) == "-6.2" and nv.fmt(None) == "—")
check("fmt: unsigned", nv.fmt(2.63, 1, sign=False) == "2.6")
check("fmt_quoted: whole cents stay whole, else one decimal", nv.fmt_quoted(11.0) == "+11" and nv.fmt_quoted(-6.204) == "-6.2")

print("the table")
tr = parse(html)
check("8 body rows and an 8-column header", len(tr) == 8 and len(re.findall(r"<th[\s>]", html)) == 8, (len(tr), len(re.findall(r"<th[\s>]", html))))
check("header names carry the reference contract", "Futures spread vs ZCZ26" in strip(html) and "Net of Interest ZCZ26" in strip(html)
      and "Basis ZCZ26" in strip(html))
check("header 8 can wrap (zero-width space after the slash)", "Inverse(+)/​Carry(−)" in html)
check("the commodity banner sits outside the scrolling table", html.index("nc-banner") < html.index('class="nc-scroll"')
      and "Corn · FOB Barge STL · Oct 05, 2026" in html)
check("the card is the portal's .sheet-wrap", html.startswith('<div class="sheet-wrap" id="snap_nc_table">'))
tops = [i for i, (cls, _c, _t) in enumerate(tr) if cls == "top"]
check("exactly one row is the top, and it is the table row net_carry named", tops == [top["row"]], (tops, top["row"]))
check("the TOP OF NET CARRY pill appears once, in that row", html.count("TOP OF NET CARRY") == 1
      and "TOP OF NET CARRY" in tr[top["row"]][2])
nets = [float(c[6]) for _cls, c, _t in tr]
check("the highlighted row holds the highest Net of Interest in the table",
      abs(nets[top["row"]] - max(nets)) < 1e-9 and nets[top["row"]] == 37.6, nets)
check("its Net of Interest is bold", "<b>+37.6</b>" in tr[top["row"]][2])
check("the other rows are plain or banded, never top", {cls for i, (cls, _c, _t) in enumerate(tr) if i != top["row"]} <= {"band", ""})
first = tr[0][1]
check("first row (the carry start): no interest cell, no carry cell", first[5] == "" and first[7] == "", first)
check("Jan row cells: delivery, contract, quoted, spread, basis REF, interest, net, carry",
      tr[3][1][:7] == ["Jan 2027", "ZCH27", "+23.6", "+14.2", "+37.8", "7.8", "+30.0"], tr[3][1])
check("carry is red when negative (the market pays to store) and green when positive (inverse)",
      "color:#c0392b" in tr[1][2] and "color:#0a7f3f" in tr[7][2], (tr[1][2][-160:], tr[7][2][-160:]))
check("a row quoted off the reference itself shows a dim 0.0 spread", 'nc-dim">0.0<' in tr[1][2])
check("no row is flagged when every contract is priced", "nc-flag" not in html)

print("a partly unpriced sheet")
part = nd.build_sheet("Corn", OCT05, COLS, {"Oct": 4.975, "Nov": 4.975, "Dec": 4.975})
rows_p, meta_p = nc.compute_net_carry(nd.items_for(part, FOB_STL), "ZCZ26", part.curve, 10, RATE)
html_p = nv.carry_table_html(rows_p, None, "ZCZ26", "t")
tr_p = parse(html_p)
check("an unpriced contract's rows carry the red dot and an em-dash spread", "nc-flag" in tr_p[3][2] and tr_p[3][1][3] == "—", tr_p[3][1])
check("no top row when none is given", all(cls != "top" for cls, _c, _t in tr_p) and "TOP OF NET CARRY" not in html_p)

print("markup safety")
for name, h in (("table", html), ("callout", nv.callout_html(top)),
                ("summary", nv.summary_html("ZCZ26", True, 497.5, 2.54, 6.13, "Oct")),
                ("kicker", nv.kicker_html("Compare locations")), ("compare card", nv.compare_card_html("<div>x</div>"))):
    check(f"{name}: one line, no blank line (Streamlit's markdown would turn it into a code block)", "\n" not in h, repr(h[:80]))
evil = nc.CarryRow(delivery="<script>x</script>", futures="ZC<Z", ym=(2026, 10), raw_basis=1.0, credit=0.0, basis_ref=1.0,
                   converted=True, months=0, days=0, interest=0.0, net=1.0, carry=None)
html_e = nv.carry_table_html([evil], None, "ZC<Z26", "<b>t</b>")
check("labels and titles are HTML-escaped", "<script>" not in html_e and "&lt;script&gt;" in html_e and "<b>t</b>" not in html_e)

print("summary line, callout, notes")
sm = strip(nv.summary_html("ZCZ26", True, 497.5, 2.54, 6.13, "Oct"))
check("summary: reference, board, monthly interest, carry start",
      sm == "Reference ZCZ26 (front delivery's futures) · board 4.97 · interest 2.54¢/mo @ 6.13% · carry from Oct", sm)
check("summary: new-crop wording", "(nearest new-crop)" in strip(nv.summary_html("ZCZ26", False, 497.5, 2.54, 6.13, "Oct")))
check("summary: no board price, no interest bits",
      "interest" not in strip(nv.summary_html("ZCZ26", True, None, None, 6.13, "Oct")))
co = strip(nv.callout_html(top))
check("callout: the headline and the detail from net_carry", co.startswith("▲ Top of net carry: Apr 27 at +37.6¢ net of interest.")
      and "gives back 2.5¢ by May 27" in co, co)
check("no top -> no callout", nv.callout_html(None) == "")
check("anchor note: silent when the chosen month is on the sheet", nv.anchor_note(meta, 10, "Oct") == "")
rows_n, meta_n = nc.compute_net_carry(items, "ZCZ26", sheet.curve, 8, RATE)
note = nv.anchor_note(meta_n, 8, "Aug")
check("anchor note: says interest starts at the first month when the sheet has no delivery in the chosen one",
      "no Aug delivery" in note and "Oct 2026" in note, note)

print("the comparison card")
peers = {"MTV": {"Oct": -0.2, "Nov": 0.1, "Dec": 0.18, "Jan": 0.2, "Feb": 0.22, "Mar": 0.25, "Apr": 0.27, "May": 0.27},
         "Cairo": {"Oct": 0.1, "Nov": 0.3, "Dec": 0.35, "Jan": 0.33, "Feb": 0.36, "Mar": 0.4, "Apr": 0.45, "May": 0.4}}
entries = [ncmp.Entry("r|STL", "STL", "basis", items, OCT05, True)] + \
          [ncmp.Entry(f"r|{n}", n, "basis", nd.items_for(sheet, row), OCT05) for n, row in peers.items()]
res = ncmp.build_comparison(entries, "ZCZ26", sheet.curve, 10, meta["anchor_ym"], RATE, "net", OCT05)
card = nv.compare_card_html(ncmp.render_html(res))
check("wrapped in the portal's card, one line", card.startswith('<div class="sheet-wrap nc-compare" id="snap_nc_compare">') and "\n" not in card)
check("the main location is starred and every column is there", "★" in card and "MTV" in card and "cairo" in card.lower() and "STL" in card)
st_top = res["columns"][0]["top"]
check("each column's top is its own maximum net (STL: Apr 27 +37.6)", st_top["label"] == "Apr 27" and abs(st_top["net"] - 37.56) < 0.05, st_top)
for c in res["columns"]:
    vals = [p["value"] for p in c["points"].values() if p["value"] is not None]
    check(f"{c['entry'].name}: the marked top equals the highest net in its column", abs(c["top"]["net"] - max(vals)) < 1e-9)
check("the stylesheet restyles the compare table to the card's width and keeps the month column in view",
      ".nc-compare table" in nv.CSS and "position: sticky" in nv.CSS)

print("\n" + ("ALL PASS" if not FAILS else "FAILURES: %s" % FAILS))
sys.exit(1 if FAILS else 0)
