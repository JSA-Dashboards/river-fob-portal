"""The River FOB daily email (river_email.py) and the shared views behind it (river_views.py). No database or network: synthetic rows only.

    python tests/test_river_email.py
"""
import datetime as dt
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

import river_email as E
import river_views as RV

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("delivery month")
D = dt.date
for commodity, today, want in [
        ("Corn", D(2026, 11, 9), 202612), ("Soybeans", D(2026, 11, 9), 202611),         # Kolten's example
        ("Corn", D(2026, 12, 15), 202612), ("Soybeans", D(2026, 12, 15), 202701),       # past soy's last contract of the year
        ("Corn", D(2027, 1, 4), 202703), ("Soybeans", D(2027, 1, 4), 202701),
        ("Corn", D(2027, 8, 31), 202709), ("Soybeans", D(2027, 8, 31), 202708),
        ("Corn", D(2027, 10, 1), 202712), ("Soybeans", D(2027, 10, 1), 202711)]:
    check(f"{commodity} on {today} -> {want}", E.delivery_key(commodity, today) == want, E.delivery_key(commodity, today))

print("segments")
segs = E.illinois_segments()
check("only the Illinois zones, Z1..Z5 in order", segs == [f"Illinois Z{i}" for i in range(1, 6)], segs)


def bid(date, provider, location, seg, cents, deliv="Dec 2026"):
    return {"date": date, "provider": provider, "location": location, "segment": seg, "deliv": deliv, "basis_cents": cents}


print("movement table")
hd = pd.DataFrame([
    bid("2026-10-07", "ADM", "Hennepin", "Illinois Z3", -1.0),
    bid("2026-10-30", "ADM", "Hennepin", "Illinois Z3", 1.0),
    bid("2026-11-05", "ADM", "Hennepin", "Illinois Z3", 2.0),
    bid("2026-11-06", "ADM", "Hennepin", "Illinois Z3", 3.0),
    bid("2026-11-05", "Cargill", "Lacon", "Illinois Z3", 4.0),
    bid("2026-11-06", "Cargill", "Lacon", "Illinois Z3", 7.0),
    bid("2026-11-06", "ADM", "Morris", "Illinois Z2", -2.0),
    bid("2026-11-06", "ADM", "Morris", "Illinois Z2", -4.0, deliv="Jan 2027"),         # another month: not this table
    bid("2026-11-06", "ADM", "St Louis", "St Louis", 9.0)])                            # another river: not this table
mv = RV.movement_table(hd, "Dec 2026", ["Illinois Z2", "Illinois Z3"])
rows = mv["rows"]
check("each zone, then its terminals", [r["Terminal"].strip() for r in rows] == [
    "▸ Illinois Z2 — segment median (1)", "ADM · Morris", "▸ Illinois Z3 — segment median (2)", "ADM · Hennepin", "Cargill · Lacon"], rows)
check("the zone is the median of its terminals", rows[2]["Current ¢"] == 5.0, rows[2])
check("terminal rows have a blank Segment; zone rows name it", rows[3]["Segment"] == "" and rows[2]["Segment"] == "Illinois Z3")
check("day / week / month against the last quoted day on or before", [b for _, b in mv["bases"]] == ["2026-11-05", "2026-10-30", "2026-10-07"], mv["bases"])
check("changes are current less then", rows[3]["Day Δ"] == 1.0 and rows[3]["Week Δ"] == 2.0 and rows[3]["Month Δ"] == 4.0, rows[3])
check("the caption names the dates", "as of 2026-11-06 · day vs 2026-11-05" in RV.movement_note(mv), RV.movement_note(mv))
check("a month nobody quotes is None", RV.movement_table(hd, "Mar 2027", ["Illinois Z3"]) is None)

print("cash vs delivery window")
df = pd.DataFrame([{"date": D(2026, 11, d), "contract": "CZ", "cents": float(d)} for d in (2, 6, 9)]
                  + [{"date": D(2026, 10, 30), "contract": "CZ", "cents": 30.0}])
w, keep = RV.cashdel_window(df, 2, D(2026, 11, 9))
check("weekly = the newest day of each week, last N weeks", keep == [D(2026, 11, 6), D(2026, 11, 9)], keep)
check("the rows are those days'", sorted(w["cents"]) == [6.0, 9.0], list(w["cents"]))
check("the frame keeps its columns", list(w.columns) == ["date", "contract", "cents"], list(w.columns))
check("nothing in the window", RV.cashdel_window(df, 2, D(2026, 1, 1))[1] == [])
check("year span", RV.year_span([D(2025, 12, 1), D(2026, 1, 1)]) == "2025–2026" and RV.year_span([D(2026, 1, 1)]) == "2026")

print("the chart")
try:
    import vl_convert as vlc
    fr = pd.DataFrame([{"date": pd.Timestamp(2026, 11, 6), "contract": "CZ", "cents": -4.0}, {"date": pd.Timestamp(2026, 10, 30), "contract": "CZ", "cents": -6.0}])
    spec = RV.cashdel_chart(fr, [D(2026, 10, 30), D(2026, 11, 6)], "Corn", "Hennepin", width=900, background="white")
    png = vlc.vegalite_to_png(spec.to_json(), scale=1.5)
    check("renders a PNG", png[:4] == b"\x89PNG" and len(png) > 5000, len(png))
    check("the title is the app's", spec.to_dict()["title"]["text"] == "Cash vs. Delivery: Corn (Hennepin) · 2026", spec.to_dict()["title"])
except ImportError:
    print("  (vl_convert not installed: skipped)")

print("html")
TOP = {"commodity": "Corn", "months": ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "June"], "contracts": ["CZ", "CZ", "CH", "CH", "CH", "CK", "CK", "CN"],
       "fut": {"Nov": 4.4225, "Dec": 4.4225}, "cif": {"Nov": 0.93, "Dec": 1.00}, "cash_label": "Cash vs Delivery (Hennepin)",
       "cash": {"Nov": -0.11, "Dec": 0.04}, "labels": ["CZ/CH", "CH/CK", "CK/CN"], "spreads": [-0.135, -0.0825, None], "carry": [0.44, -0.40, 0.37],
       "prior": {"cif": {"Nov": 0.90, "Dec": 1.05}, "cash": {"Nov": -0.11}}, "de": {"Hennepin": 12.25}}
html = E.sheet_html(TOP, D(2026, 11, 6))
check("date bar", "FRIDAY, NOVEMBER 6, 2026" in html, "")
check("banner in the commodity's colour, solid", ">Corn<" in html and E.BANNER["Corn"] in html and "gradient" not in html, "")
check("Del Equiv column in the header", "Del Equiv" in html)
check("CBOT 4 places", "4.4225" in html)
check("negatives in brackets", "(0.11)" in html and "(0.1350)" in html, "")
check("the positive stays bare", ">0.04<" in html, "")
check("% Full Carry", ">44%<" in html and ">-40%<" in html, "")
check("up and down are shaded", E.UP in html and E.DOWN in html, "")
col_counts = {h.count("<td") for h in html.split("<tr>")[3:] if "colspan" not in h}
check("every full row has the same number of cells", col_counts == {10}, col_counts)

MV = {"label": "Dec 2026", "on_target": True, "table": mv}
out = E.movement_html("Corn", MV)
check("title names the month", "Corn: Dec 2026" in out, "")
check("zone row and terminals", "segment median (2)" in out and "ADM · Hennepin" in out, "")
check("Δ signed", "+1.0" in out, "")
check("zero isn't signed", "+0.0" not in out and "-0.0" not in out, "")
check("the caption is there", "positive = basis firmed" in out, "")
check("a stand-in month says so", "next quoted month" in E.movement_html("Corn", {**MV, "on_target": False}), "")
check("a name with & is escaped", "A &amp; B" in E.movement_html("Corn", {**MV, "table": {**mv, "rows": [{**rows[3], "Terminal": "A & B"}]}}), "")

charts = {"Corn": b"\x89PNG-corn", "Soybeans": None}
tops = {"Corn": TOP, "Soybeans": {**TOP, "commodity": "Soybeans"}}
moves = {"Corn": MV, "Soybeans": None}
body = E.build_html(D(2026, 11, 6), tops, charts, moves, '<div>John Stewart SIG<img src="cid:sig_1"></div>')
check("the chart is an inline attachment", 'src="cid:cashdel_corn"' in body, "")
check("the signature comes last", body.index("John Stewart SIG") > body.index("No Illinois Soybeans bids"), "")
check("no signature, no block", "SIG" not in E.build_html(D(2026, 11, 6), tops, charts, moves), "")
check("a missing chart says so", "No Cash vs Delivery history for Soybeans" in body, "")
check("missing bids say so", "No Illinois Soybeans bids" in body, "")
check("in order: sheets, charts, movement", body.index(">Corn<") < body.index(">Soybeans<") < body.index("cid:cashdel_corn") < body.index("Corn: Dec 2026"), "")
check("declares utf-8", "charset=utf-8" in body, "")
check("no remote pictures or scripts", 'src="http' not in body and "<script" not in body, "")
prev = E.embed_images(body, {"cashdel_corn": b"\x89PNG-corn", "sig_1": b"s"})
check("a preview embeds every picture", "data:image/png;base64," in prev and "cid:" not in prev, "")
check("subject", E.subject(D(2026, 11, 6)) == "River FOB Daily: Fri Nov 6, 2026", E.subject(D(2026, 11, 6)))

print("when it sends")
check("no sheet: wait", E.decide(False, False, False) == "wait")
check("no sheet on the last run: tell Kolten", E.decide(False, False, True) == "notify")
check("sheet, no bids: wait", E.decide(True, False, False) == "wait")
check("sheet and bids: send", E.decide(True, True, False) == "send")
check("sheet, no bids on the last run: send anyway", E.decide(True, False, True) == "send_stale")
mk = lambda latest: {"table": {"latest": latest}}
check("bids are current only when both grains are today's", E.bids_current({"Corn": mk("2026-11-09"), "Soybeans": mk("2026-11-09")}, "2026-11-09")
      and not E.bids_current({"Corn": mk("2026-11-09"), "Soybeans": mk("2026-11-06")}, "2026-11-09")
      and not E.bids_current({"Corn": mk("2026-11-09"), "Soybeans": None}, "2026-11-09"), "")
check("futures must be there", E.has_futures("Corn", ({"Corn": {"Nov": 4.4}}, {})) and not E.has_futures("Corn", ({"Corn": {"Nov": None}}, {}))
      and not E.has_futures("Corn", ({}, {})), "")

print("state and failures")
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "sub", "state.json")
    check("no state file is empty", E.load_state(p) == {})
    E.save_state({"done_day": "2026-11-09"}, p)
    check("round trip", E.load_state(p) == {"done_day": "2026-11-09"})
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("a corrupt file is empty, not a crash", E.load_state(p) == {})

    real = E.save_state
    E.save_state = lambda s, path=p: real(s, p)
    st = {}
    codes = [E.report_failure(st, "2026-11-09", "boom", False) for _ in range(16)]
    check("quiet, then the 3rd failure, then every 12th", [i + 1 for i, c in enumerate(codes) if c] == [3, 15], codes)
    check("a new day starts over", E.report_failure(st, "2026-11-10", "boom", False) == 0 and st["fails"] == 1, st)
    check("the last run always reports", E.report_failure({}, "2026-11-10", "boom", True) == 3)
    E.save_state = real

print("signature")
with tempfile.TemporaryDirectory() as tmp:
    def expect_error(folder):
        try:
            E.load_signature(folder)
        except RuntimeError:
            return True
        return False

    check("no folder refuses", expect_error(os.path.join(tmp, "none")))
    with open(os.path.join(tmp, "signature.html"), "w", encoding="utf-8") as f:
        f.write('<div>John Stewart &amp; Associates<img src="cid:sig_1"><img src="cid:sig_1"></div>')
    check("a picture missing refuses", expect_error(tmp))
    with open(os.path.join(tmp, "sig_1.png"), "wb") as f:
        f.write(b"png")
    html_sig, images = E.load_signature(tmp)
    check("reads the html and each picture once", "John Stewart" in html_sig and images == {"sig_1": b"png"}, images)
    with open(os.path.join(tmp, "signature.html"), "w", encoding="utf-8") as f:
        f.write("<div>empty</div>")
    check("something else in the file refuses", expect_error(tmp))

real_load = E.load_signature
E.load_signature = lambda folder=None: ("<div>SIG</div>", {"sig_1": b"s"})
subj, html_c, imgs = E.compose({"as_of": D(2026, 11, 6), "tops": tops, "charts": charts, "moves": moves})
E.load_signature = real_load
check("compose: charts that exist plus the signature's pictures", set(imgs) == {"cashdel_corn", "sig_1"}, set(imgs))
check("compose: the signature is in the body", "<div>SIG</div>" in html_c and subj.startswith("River FOB Daily"), "")

print("whatsapp")
W = E.WA
cell_rows = E.sheet_rows(TOP, D(2026, 11, 6))
svg = W.table_svg(cell_rows, E.sheet_widths(TOP), note="Green shade")
check("the picture shows what the email shows", all(t in svg for t in ("FRIDAY, NOVEMBER 6, 2026", "Del Equiv", "4.4225", "(0.1350)", "44%", "Green shade")), "")
check("each full row fits its widths", all(sum(c["span"] for c in r) == len(E.sheet_widths(TOP)) for r in cell_rows), [sum(c["span"] for c in r) for r in cell_rows])
check("the same shading as the email", E.UP in svg and E.DOWN in svg, "")
_, mv_rows, mv_note = E.movement_parts("Corn", MV)
check("movement rows: header + each table row", len(mv_rows) == 1 + len(rows), len(mv_rows))
check("the zone marker stays out of the picture", "▸" not in W.table_svg(mv_rows, [560] + [120] * 4) and "segment median (2)" in W.table_svg(mv_rows, [560] + [120] * 4), "")
check("text is escaped in the picture","A &amp; B" in W.table_svg([[E._cell("A & B")]], [100]), "")
try:
    import vl_convert
    d_png = W.table_png(mv_rows, [560] + [120] * 4, title="Corn: Dec 2026", note=mv_note)
    check("draws a PNG", d_png[:4] == b"\x89PNG" and len(d_png) > 5000, len(d_png))
    fake = {"as_of": D(2026, 11, 6), "tops": tops, "charts": charts, "moves": moves}
    names = [n for n, _ in E.whatsapp_pngs(fake)]
    check("pictures in the email's order, skipping what's missing", names == ["1_sheet_corn", "1_sheet_soybeans", "2_cash_vs_delivery_corn", "3_movement_corn"], names)
except ImportError:
    print("  (vl_convert not installed: skipped)")
sig_html = '<div><b>John Stewart</b></div><p><span>Trading commodity futures, options &amp; more.&nbsp;Past\n results. &copy; JSA</span></p>'
check("the disclaimer comes out as plain text", W.disclaimer(sig_html) == "Trading commodity futures, options & more. Past results. © JSA", W.disclaimer(sig_html))
check("no disclaimer in the signature is empty", W.disclaimer("<div>John Stewart</div>") == "")

os.environ.pop("RIVER_WA_CHATS", None)
check("no chats set: WhatsApp is off", W.config() is None)
os.environ.update(RIVER_WA_CHATS="a@g.us, b@g.us", WA_API_KEY="k")
cfg = W.config()
check("chats and the default sender", cfg["chats"] == ["a@g.us", "b@g.us"] and cfg["session"] == "Kolten Postin", cfg)
os.environ.pop("RIVER_WA_CHATS"), os.environ.pop("WA_API_KEY")

import requests as _rq
calls = []
script = []


class WR:
    def __init__(self, code=200, results=None):
        self.status_code = code
        self.res = results

    def json(self):
        return {"results": self.res if self.res is not None else [{"ok": True}]}


def wa_post(url, json=None, timeout=None, headers=None):
    calls.append((url, json, headers))
    r = script.pop(0)
    if isinstance(r, Exception):
        raise r
    return r


real_post2 = _rq.post
_rq.post = wa_post
wcfg = {"chats": ["g@g.us"], "session": "Kolten Postin", "key": "k"}
try:
    script[:] = [WR(), WR()]
    W.send(wcfg, wcfg["chats"], [b"a", b"b"], "cap", "closing")
    check("pictures, then the closing text", len(calls) == 2 and len(calls[0][1]["images"]) == 2 and calls[0][1]["message"] == "cap" and calls[1][1] == {"chat_ids": ["g@g.us"], "message": "closing"}, calls)
    check("as the chosen account, with the key", calls[0][2] == {"x-api-key": "k", "x-session-id": "Kolten Postin"}, calls[0][2])

    def outcome(*responses):
        script[:] = list(responses)
        try:
            W.send(wcfg, wcfg["chats"], [b"a"], "cap", "closing")
            return "ok"
        except Exception as e:
            return type(e).__name__

    check("account not connected: safe to retry", outcome(WR(503)) == "NotReady")
    check("bridge down: safe to retry", outcome(_rq.ConnectionError("x")) == "NotReady")
    check("a timeout mid-post isn't safe to retry", outcome(_rq.ReadTimeout("x")) == "ReadTimeout")
    check("refused by WhatsApp isn't safe to retry", outcome(WR(200, [{"ok": False, "error": "e"}])) == "RuntimeError")
    check("pictures posted, closing not ready: not safe to retry", outcome(WR(), WR(503)) == "RuntimeError")
    calls.clear()
    try:
        W.send({**wcfg, "key": ""}, wcfg["chats"], [b"a"], "c")
        no_key = "ok"
    except W.NotReady:
        no_key = "NotReady"
    check("no key refuses before posting", no_key == "NotReady" and not calls, no_key)

    with tempfile.TemporaryDirectory() as tmp:
        p2 = os.path.join(tmp, "state.json")
        real_state, real_parts = E.save_state, E.whatsapp_parts
        E.save_state, E.whatsapp_parts = (lambda s, path=p2: real_state(s, p2)), (lambda d: ([b"a"], "cap", "closing"))
        st2 = {}
        script[:] = [WR(503)]
        try:
            E.whatsapp_once(wcfg, {}, st2, "2026-11-09")
        except W.NotReady:
            pass
        check("not connected: today stays open for the next run", "wa_day" not in st2, st2)
        script[:] = [WR(200, [{"ok": False, "error": "e"}])]
        try:
            E.whatsapp_once(wcfg, {}, st2, "2026-11-09")
            raised = False
        except W.MaybeSent:
            raised = True
        check("may have posted: today is finished, and it's reported", raised and st2.get("wa_day") == "2026-11-09", st2)
        st3 = {}
        script[:] = [WR(), WR()]
        E.whatsapp_once(wcfg, {}, st3, "2026-11-09")
        check("posted: today is finished", st3.get("wa_day") == "2026-11-09", st3)
        E.save_state, E.whatsapp_parts = real_state, real_parts
finally:
    _rq.post = real_post2

print("sending")
sent = {}


class R:
    status_code = 202
    text = ""


def post(url, headers=None, data=None, timeout=None):
    sent.update(url=url, msg=json.loads(data))
    return R()


import requests
real_post, real_token = requests.post, E.CB._token
requests.post, E.CB._token = post, lambda cfg: "t"
try:
    E.send({"GRAPH_SENDER": "x@jpsi.com"}, ["list@jpsi.com"], "S", "<b>", {"cashdel_corn": b"abc"})
finally:
    requests.post, E.CB._token = real_post, real_token
m = sent["msg"]["message"]
check("from the sender's mailbox", "/users/x%40jpsi.com/sendMail" in sent["url"], sent["url"])
check("to the list", [r["emailAddress"]["address"] for r in m["toRecipients"]] == ["list@jpsi.com"], "")
check("one inline attachment per picture", len(m["attachments"]) == 1 and m["attachments"][0]["isInline"] and m["attachments"][0]["contentId"] == "cashdel_corn", m["attachments"])
check("replies go to a person", m["replyTo"][0]["emailAddress"]["address"] == E.REPLY_TO, "")
check("not filed in the shared mailbox's Sent Items", sent["msg"]["saveToSentItems"] is False, "")
check("a send without a list has no BCC (the --to test)", m["bccRecipients"] == [], m["bccRecipients"])

requests.post, E.CB._token = post, lambda cfg: "t"
try:
    E.send({"GRAPH_SENDER": "x@jpsi.com"}, ["kolten@jpsi.com"], "S", "<b>", {}, bcc=["list@jpsi.com"])
finally:
    requests.post, E.CB._token = real_post, real_token
m = sent["msg"]["message"]
check("the list is on BCC only", [r["emailAddress"]["address"] for r in m["bccRecipients"]] == ["list@jpsi.com"]
      and [r["emailAddress"]["address"] for r in m["toRecipients"]] == ["kolten@jpsi.com"], m)

print("recipients")
for k in ("RIVER_EMAIL_TO", "RIVER_EMAIL_BCC"):
    os.environ.pop(k, None)
check("nothing set: nobody, so the job is off", E.recipients() == ([], []), E.recipients())
os.environ["RIVER_EMAIL_BCC"] = "list@jpsi.com"
check("the list set: Kolten on To, the list on BCC", E.recipients() == ([E.NOTIFY], ["list@jpsi.com"]), E.recipients())
check("the list is not on To", "list@jpsi.com" not in E.recipients()[0])
os.environ["RIVER_EMAIL_TO"] = "other@jpsi.com"
check("RIVER_EMAIL_TO overrides the To only", E.recipients() == (["other@jpsi.com"], ["list@jpsi.com"]), E.recipients())
for k in ("RIVER_EMAIL_TO", "RIVER_EMAIL_BCC"):
    os.environ.pop(k, None)

print("\n" + ("ALL PASS" if not FAILS else f"FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
