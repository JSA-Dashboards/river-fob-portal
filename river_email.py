"""
river_email.py: the River FOB daily email, sent once a trading day to JSA staff when the day's sheet is saved.

Kolten 2026-11-09: "an email to go out to a specific distribution list", from his screenshots. In order:
  1. the top of the Corn sheet and the Soybeans sheet (date through % Full Carry);
  2. the Cash vs. Delivery charts, Corn and Beans (Hennepin; 12 weekly points, as the app opens);
  3. River Bids, Movement by segment for the upcoming delivery month: the Illinois zones with their terminals, nothing else;
  4. Kolten's signature (email_signature/, kept out of git; no signature, no email).

The numbers come from the code the app draws with (fob_model, river_views), the saved sheet, and the sidebar's defaults: the seed cash
distance and storage, and interest at fed funds + 2.25%. Upcoming delivery month = the first contract month on or after this calendar
month (in November: Corn Dec, Soybeans Nov).

Runs on the Droplet every 10 minutes, 4:05 to 7:55 PM CT on weekdays (deploy/run_river_email.sh under cron-alert; the bid import ends about
4:00). It sends the first time today's sheet is saved with its futures and today's Illinois bids are in. The last run sends without today's
bids if only those are missing (the as-of date shows it), or tells Kolten nothing went out if there's no sheet. A sheet saved again after the
send doesn't send again.

    python river_email.py                     # the scheduled run (does nothing until RIVER_EMAIL_BCC is set)
    python river_email.py --preview out.html  # write the newest sheet's email to a file; send nothing
    python river_email.py --dry-run           # say what today's run would do; send nothing
    python river_email.py --to a@jpsi.com     # send the newest sheet's email to that address only; no state kept
    python river_email.py --pngs DIR          # write the WhatsApp pictures to a folder; send nothing
    python river_email.py --wa-to CHAT_ID     # post the newest sheet's pictures to that WhatsApp chat only; no state kept

Env (the repo .env on the droplet): USE_SNOWFLAKE + SNOWFLAKE_*, GRAPH_* (check_bidsheet_fresh._graph_cfg), MASSIVE_API_KEY (fed funds),
RIVER_EMAIL_BCC (the list, comma-separated; Kolten: "move the list to BCC like Inco"), RIVER_EMAIL_TO (overrides the To, which is Kolten), RIVER_WA_CHATS + WA_API_KEY (river_whatsapp.py).

Exit codes: 0 = sent, not due, or a failure the next run will retry; 3 = the third failure of a day, every 12th after, or a failure on the last
run (cron-alert reports those, so a blip doesn't email anyone).
"""
import argparse
import base64
import datetime as dt
import io
import json
import logging
import os
import re
import sys
from html import escape
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"), override=True)
except ImportError:
    pass

import pandas as pd

import bids_data
import check_bidsheet_fresh as CB
import db
import delivery_period as DP
import fob_model as M
import massive_futures
import river_segments as RS
import river_views as RV
import river_whatsapp as WA
import seed_data as S

log = logging.getLogger("river_email")

COMMODITIES = ("Corn", "Soybeans")
REPLY_TO = "kpostin@jpsi.com"
NOTIFY = "kpostin@jpsi.com"              # told when the last run has nothing to send
STATE = os.path.join(HERE, "logs", ".river_email_state.json")
LAST_RUN = dt.time(19, 55)
CHART_WEEKS = 12
CHART_WIDTH = 900
CHART_SCALE = 1.5
BIDS_DAYS = 120
WATERMARK = os.path.join(HERE, "assets", "jsa_50yr.png")
SIGNATURE_DIR = os.path.join(HERE, "email_signature")

# Same colours as the app's sheet, solid: Outlook doesn't draw gradients.
DARK = "#32373c"
BLUE = "#0693e3"
BANNER = {"Corn": "#e09600", "Soybeans": "#3e7d33"}
UP = "#e8f5e9"
DOWN = "#ffebee"
NEG = "#c00000"
GREY = "#6b7280"
LINE = "#e5e7eb"
FONT = "font-family:Segoe UI,Arial,sans-serif;"


# ── What goes in it ───────────────────────────────────────────────────────────────────────────────
def delivery_key(commodity, today):
    """The upcoming delivery month as 100*year + month: the first contract month on or after this calendar month."""
    cycle = M.CONTRACT_CYCLE[commodity]
    later = [m for m in cycle if m >= today.month]
    return today.year * 100 + min(later) if later else (today.year + 1) * 100 + min(cycle)


def illinois_segments():
    return [s for s in RS.SEGMENT_ORDER if s.startswith("Illinois")]


def interest_pct(as_of):
    """The sidebar's default: fed funds + 2.25%, or the seed when the rate can't be read."""
    try:
        ff = massive_futures.fed_funds_rate(as_of)
    except Exception:
        ff = None
    return S.SEED_INTEREST_PCT if ff is None else round(ff + massive_futures.FED_FUNDS_SPREAD_PCT, 2)


def _prior(commodity, as_of_iso, dates, cash_c):
    """The newest saved sheet before this one, for the day's green / red shading (the app's load_prior)."""
    pdate = next((d for d in dates if d < as_of_iso), None)
    if not pdate:
        return {}
    cif, frt, cal = db.load_snapshot(pdate)
    if cif is None:
        return {}
    cols = (cal or {}).get(commodity)
    months = [m for m, _ in cols] if cols else M.MONTHS
    cifc = cif.get(commodity, {}) or {}
    grid = M.compute_fob_grid(commodity, cifc, frt, months)
    cash = M.cash_vs_delivery(commodity, grid[M.CARRY_CONFIG[commodity]["cash_loc"]], cash_c, months)
    return {"cif": cifc, "cash": dict(zip(months, cash))}


def sheet_top(commodity, as_of_iso, dates, snapshot, extras, rate):
    """Everything the top of one commodity's sheet shows, as of a saved date."""
    cif, frt, cal = snapshot
    fut, spr = extras
    cols = (cal or {}).get(commodity)
    months = [m for m, _ in cols] if cols else M.MONTHS
    contracts = [ct for _, ct in cols] if cols else list(M.CONTRACTS[commodity])
    cif_row = (cif or {}).get(commodity) or {}
    fbr = {r: (frt.get(r) or {}) for r in M.FREIGHT_REGIONS}
    fut_row = (fut or {}).get(commodity) or {}
    cash_c = S.SEED_CASH_C[commodity]
    pairs = dict((spr or {}).get(commodity) or [])
    labels = M.spread_labels_for(commodity, contracts)
    spreads = [pairs.get(l) for l in labels]
    full = (M.compute_full_carry(commodity, fut_row, rate / 100.0, S.SEED_STORAGE_MO[commodity],
                                 contracts=contracts, months=months) if fut_row else [])
    cfg = M.CARRY_CONFIG[commodity]
    grid = M.compute_fob_grid(commodity, cif_row, fbr, months)
    return {"commodity": commodity, "months": months, "contracts": contracts, "fut": fut_row, "cif": cif_row,
            "cash_label": cfg["cash_label"], "cash": dict(zip(months, M.cash_vs_delivery(commodity, grid[cfg["cash_loc"]], cash_c, months))),
            "labels": labels, "spreads": spreads, "carry": M.pct_full_carry(spreads, full) if full else [],
            "prior": _prior(commodity, as_of_iso, dates, cash_c), "de": M.DELIVERY_EQUIV.get(commodity, {})}


def movement(commodity, today):
    """(the delivery month's label, its Movement table for the Illinois zones, whether that's the month the rule picked).
    When nobody quotes the month yet, the next quoted one stands in."""
    hist = bids_data.bid_history(commodity, (today - dt.timedelta(days=BIDS_DAYS)).isoformat())
    if not hist:
        return None
    hd = pd.DataFrame(hist)
    RV.decorate(hd)
    hd["date"] = hd["timestamp"].str[:10]
    segs = illinois_segments()
    hd = hd[hd["segment"].isin(segs)]
    target = delivery_key(commodity, today)
    keys = sorted(k for k in hd["dkey"].dropna().unique() if k >= target)
    if not keys:
        return None
    key = int(keys[0])
    sel = DP.label((key // 100, key % 100))
    mv = RV.movement_table(hd, sel, segs)
    return None if mv is None else {"label": sel, "table": mv, "on_target": key == target}


def chart_png(commodity):
    """The Cash vs Delivery chart as a PNG, watermarked as the app's page is. None when there's no history."""
    import altair as alt
    import vl_convert as vlc
    from PIL import Image

    df = RV.cashdel_frame(commodity, S.SEED_CASH_C[commodity])
    if df.empty:
        return None
    df, keep = RV.cashdel_window(df, CHART_WEEKS, df["date"].max())
    if not keep:
        return None
    # Real timestamps, so a date is read in local time: a bare 'YYYY-MM-DD' is UTC and labels the day before on a Central-time box.
    df = df.assign(date=pd.to_datetime(df["date"]))
    alt.data_transformers.disable_max_rows()
    chart = RV.cashdel_chart(df, keep, commodity, M.CARRY_CONFIG[commodity]["cash_loc"], width=CHART_WIDTH, background="white")
    png = vlc.vegalite_to_png(chart.to_json(), scale=CHART_SCALE)
    if os.path.isfile(WATERMARK):
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        mark = Image.open(WATERMARK).convert("RGBA")
        w = int(img.width * 0.30)
        mark = mark.resize((w, int(mark.height * w / mark.width)))
        mark.putalpha(mark.getchannel("A").point(lambda a: int(a * 0.11)))
        img.alpha_composite(mark, ((img.width - w) // 2, int(img.height * 0.48 - mark.height / 2)))
        out = io.BytesIO()
        img.convert("RGB").save(out, "PNG", optimize=True)
        png = out.getvalue()
    return png


# ── The email's HTML (inline styles and tables only: Outlook ignores the rest) ────────────────────
def _blank(v):
    try:
        return v is None or pd.isna(v)
    except TypeError:
        return False


def _shade(cur, prior):
    if _blank(cur) or _blank(prior):
        return ""
    return UP if cur > prior else DOWN if cur < prior else ""


def _cell(text="", align="right", bg="", color="#333", bold=False, italic=False, span=1, size=13, indent=0, border=True, pad="6px 8px"):
    """One table cell as plain data: the email's HTML and the WhatsApp PNGs are both drawn from these."""
    return {"text": str(text), "align": align, "bg": bg, "color": color, "bold": bold, "italic": italic, "span": span, "size": size,
            "indent": indent, "border": border, "pad": pad}


def _td(c):
    span = f' colspan="{c["span"]}"' if c["span"] > 1 else ""
    attr = f' bgcolor="{c["bg"]}"' if c["bg"] else ""
    style = (f'padding:{c["pad"]};text-align:{c["align"]};color:{c["color"]};font-size:{c["size"]}px;'
             + (f"border-bottom:1px solid {LINE};" if c["border"] else "")
             + ("font-weight:700;" if c["bold"] else "") + ("font-style:italic;" if c["italic"] else "")
             + (f'background-color:{c["bg"]};' if c["bg"] else "")
             + (f'padding-left:{8 + c["indent"]}px;' if c["indent"] else "")
             + ("white-space:nowrap;" if c["align"] == "right" else ""))
    return f'<td{span}{attr} style="{style}">{escape(c["text"])}</td>'


def _html_rows(rows):
    return "".join("<tr>" + "".join(_td(c) for c in row) + "</tr>" for row in rows)


def _head(text, align="center"):
    return _cell(text, align, bg=DARK, color="#ffffff", bold=True, size=12, border=False, pad="7px 6px")


def _band(text, span, bg, size=15):
    return [_cell(text, "center", bg=bg, color="#ffffff", bold=True, span=span, size=size, border=False, pad="8px")]


def _table(rows, width="100%"):
    return (f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;{FONT}width:{width};">{"".join(rows)}</table>')


def _section(title):
    return (f'<tr><td style="{FONT}padding:26px 0 8px 0;font-size:17px;font-weight:700;color:{DARK};'
            f'border-bottom:2px solid {BLUE};">{escape(title)}</td></tr>')


def _block(html, pad="10px 0 0 0"):
    return f'<tr><td style="padding:{pad};">{html}</td></tr>'


def _money(v):
    return "" if _blank(v) else f"({abs(v):.2f})" if v < 0 else f"{v:.2f}"


def sheet_rows(top, as_of):
    """The top of a commodity's sheet: date, banner, months, contracts, CBOT, CIF, Cash vs Delivery, Spreads, % Full Carry."""
    months, c = top["months"], top["commodity"]
    show_de = bool(top["de"])
    n_col = len(months) + 1 + (1 if show_de else 0)
    de_blank = [_cell()] if show_de else []
    prior = top["prior"]
    label = lambda t: _cell(t, "left", color="#2c3e50", bold=True)
    strong = lambda text, bg: _cell(text, bg=bg, bold=True, color="#1f2328")
    day = f"{as_of:%A, %B} {as_of.day}, {as_of.year}".upper()
    rows = [[_cell(day, "center", color="#7a828b", bold=True, span=n_col, size=12, border=False, pad="8px")],
            _band(c, n_col, BANNER[c], 16),
            [_head("")] + ([_head("Del Equiv")] if show_de else []) + [_head(m) for m in months],
            [_cell()] + de_blank + [_cell(ct or "", "center", color=GREY, bold=True) for ct in top["contracts"]]]
    if any(top["fut"].get(m) is not None for m in months):
        rows.append([label("CBOT")] + de_blank
                    + [_cell("" if _blank(top["fut"].get(m)) else f"{top['fut'][m]:.4f}", bg="#fafbfc") for m in months])
    p_cif = prior.get("cif", {})
    rows.append([label("CIF")] + de_blank
                + [strong("" if _blank(top["cif"].get(m)) else f"{top['cif'][m]:.2f}", _shade(top["cif"].get(m), p_cif.get(m))) for m in months])
    rows.append(_band("Cash vs Delivery", n_col, BLUE, 13))
    p_cash = prior.get("cash", {})
    rows.append([label(top["cash_label"])] + de_blank
                + [strong(_money(top["cash"].get(m)), _shade(top["cash"].get(m), p_cash.get(m))) for m in months])

    labels, spreads = top["labels"], top["spreads"]
    if any(s is not None for s in spreads):
        n = len(labels)
        pad = max(0, len(months) - 2 * n)
        cells = [_cell() for _ in range(pad)]
        for i in range(n):
            cells.append(_cell(labels[i], color=GREY, italic=True, bold=True))
            v = spreads[i] if i < len(spreads) else None
            cells.append(_cell("" if _blank(v) else f"({abs(v):.4f})" if v < 0 else f"{v:.4f}", color=NEG if not _blank(v) and v < 0 else "#333", bold=True))
        rows.append([label("Spreads")] + de_blank + cells)
        carry = [_cell() for _ in months]
        for i in range(min(n, len(top["carry"]))):
            pos = pad + 2 * i + 1
            v = top["carry"][i]
            if pos < len(carry) and not _blank(v):
                carry[pos] = _cell(f"{v * 100:.0f}%", color=NEG if v < 0 else "#333", bold=True)
        rows.append([label("% Full Carry")] + de_blank + carry)
    return rows


def sheet_html(top, as_of):
    return _table([_html_rows(sheet_rows(top, as_of))])


def sheet_widths(top):
    return [290] + ([100] if top["de"] else []) + [100] * len(top["months"])


def movement_parts(commodity, mv):
    """(title, rows, caption) of one commodity's Illinois zones, each zone's median with its terminals beneath it."""
    t = mv["table"]
    cols = [c for c in ("Current ¢", "Day Δ", "Week Δ", "Month Δ") if any(c in r for r in t["rows"])]

    def val(r, c):
        v = r.get(c)
        if _blank(v):
            return "", "#333"
        if c == "Current ¢":
            return f"{v:.1f}", "#1f2328"
        v = round(float(v), 1)
        return (f"{v:+.1f}" if v else "0.0"), ("#0d7f3d" if v > 0 else NEG if v < 0 else "#333")

    rows = [[_head("Zone / terminal", "left")] + [_head(c, "right") for c in cols]]
    for r in t["rows"]:
        zone = bool(r["Segment"])
        bg = "#eaf4fc" if zone else ""
        first = _cell(r["Terminal"].strip(), "left", bg=bg, bold=zone, color="#1f2328", indent=0 if zone else 18)
        rows.append([first] + [_cell(val(r, c)[0], bg=bg, bold=zone, color=val(r, c)[1]) for c in cols])
    swapped = "" if mv["on_target"] else " (the upcoming month isn't quoted yet; this is the next quoted month)"
    return f"{commodity}: {mv['label']}", rows, f"{RV.movement_note(t)}{swapped}"


def movement_html(commodity, mv):
    title, rows, note = movement_parts(commodity, mv)
    return (f'<div style="{FONT}font-size:15px;font-weight:700;color:{BANNER[commodity]};padding:4px 0 6px 0;">{escape(title)}</div>'
            + _table([_html_rows(rows)])
            + f'<div style="{FONT}font-size:11px;color:{GREY};padding:6px 0 0 0;">{escape(note)}</div>')


def build_html(as_of, tops, charts, moves, signature=""):
    """-> the email body, its pictures referenced as cid:. `charts` = {commodity: png}; `signature` = its html, last in the email."""
    def img(c):
        if not charts.get(c):
            return (f'<div style="{FONT}font-size:13px;color:{GREY};padding:10px 0;">No Cash vs Delivery history for {escape(c)}.</div>')
        alt_text = f"Cash vs. Delivery: {RV.CHART_LABEL[c]}"
        return (f'<img src="cid:{chart_cid(c)}" width="{CHART_WIDTH}" alt="{escape(alt_text)}" '
                f'style="display:block;width:100%;max-width:{CHART_WIDTH}px;height:auto;border:0;">')

    legend = (f'<table role="presentation" align="center" cellpadding="0" cellspacing="0" border="0" style="{FONT}"><tr>'
              f'<td width="14" bgcolor="{UP}" style="background-color:{UP};border:1px solid #0d7f3d;font-size:10px;">&nbsp;</td>'
              f'<td style="font-size:12px;color:#555;padding:0 22px 0 6px;">Green shade = daily move higher</td>'
              f'<td width="14" bgcolor="{DOWN}" style="background-color:{DOWN};border:1px solid {NEG};font-size:10px;">&nbsp;</td>'
              f'<td style="font-size:12px;color:#555;padding:0 0 0 6px;">Red shade = daily move lower</td></tr></table>')
    parts = [_block(f'<div style="{FONT}font-size:20px;font-weight:700;color:{DARK};">River FOB Daily</div>', "0"), _block(legend, "12px 0 2px 0")]
    for c in COMMODITIES:
        parts.append(_block(sheet_html(tops[c], as_of), "12px 0 0 0"))
    parts.append(_section("Cash vs. Delivery"))
    for c in COMMODITIES:
        parts.append(_block(img(c), "12px 0 0 0"))
    parts.append(_section("River Bids: Movement by segment, Illinois zones"))
    for c in COMMODITIES:
        mv = moves.get(c)
        parts.append(_block(movement_html(c, mv) if mv else f'<div style="{FONT}font-size:13px;color:{GREY};">No Illinois {escape(c)} bids for the upcoming delivery month.</div>', "12px 0 6px 0"))
    if signature:
        parts.append(_block(signature, "30px 0 0 0"))
    return ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head>'
            '<body style="margin:0;padding:0;background-color:#ffffff;">'
            f'<table role="presentation" width="{CHART_WIDTH + 40}" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;max-width:{CHART_WIDTH + 40}px;border-collapse:collapse;"><tr><td style="padding:16px 20px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">{"".join(parts)}</table>'
            '</td></tr></table></body></html>')


def chart_cid(commodity):
    return f"cashdel_{commodity.lower()}"


def load_signature(folder=None):
    """(Kolten's signature html, {cid: png}). email_signature/ is NOT in git (this repo is public and the signature holds his contact links): it's copied
    to the droplet by hand. Raises when it's missing or incomplete, because the signature carries the firm's disclaimer: better no email than one without it."""
    folder = folder or SIGNATURE_DIR
    try:
        with open(os.path.join(folder, "signature.html"), encoding="utf-8") as f:
            html = f.read()
        images = {}
        for cid in sorted(set(re.findall(r'src="cid:([^"]+)"', html))):
            with open(os.path.join(folder, f"{cid}.png"), "rb") as f:
                images[cid] = f.read()
    except OSError as e:
        raise RuntimeError(f"The email signature is missing or incomplete in {folder}: {e}") from e
    if "John Stewart" not in html:
        raise RuntimeError(f"{folder}/signature.html doesn't look like the signature")
    return html, images


def compose(d):
    """(subject, html, {cid: png}) for what gather() read."""
    signature, images = load_signature()
    images = {**{chart_cid(c): png for c, png in d["charts"].items() if png}, **images}
    return subject(d["as_of"]), build_html(d["as_of"], d["tops"], d["charts"], d["moves"], signature), images


def whatsapp_pngs(d):
    """[(name, png)] in the email's order: the two sheets, the two charts, the two Movement tables. Each stands alone in a chat."""
    out = []
    legend = "Green shade = daily move higher  ·  Red shade = daily move lower"
    for c in COMMODITIES:
        top = d["tops"][c]
        out.append((f"1_sheet_{c.lower()}", WA.table_png(sheet_rows(top, d["as_of"]), sheet_widths(top), note=legend)))
    for c in COMMODITIES:
        if d["charts"].get(c):
            out.append((f"2_cash_vs_delivery_{c.lower()}", d["charts"][c]))
    for c in COMMODITIES:
        if d["moves"].get(c):
            title, rows, note = movement_parts(c, d["moves"][c])
            out.append((f"3_movement_{c.lower()}", WA.table_png(rows, [560] + [120] * (len(rows[0]) - 1), title=f"{title}  ·  River Bids, Illinois zones",
                                                                title_color=BANNER[c], note=note)))
    return out


def whatsapp_parts(d):
    """(pictures, caption, closing text). No disclaimer, no post: the same rule as the email's signature."""
    closing = WA.disclaimer(load_signature()[0])
    if not closing:
        raise RuntimeError("The disclaimer isn't in the email signature")
    return [png for _, png in whatsapp_pngs(d)], subject(d["as_of"]), closing


def whatsapp_once(wa, d, state, day):
    """Post today's pictures. If the bridge took nothing, the next run tries again; if it may have posted anything, today is finished, because a chat shouldn't get it twice."""
    parts = whatsapp_parts(d)
    try:
        WA.send(wa, wa["chats"], *parts)
    except WA.NotReady:
        raise
    except Exception as e:
        state["wa_day"] = day
        save_state(state)
        raise WA.MaybeSent(str(e)) from e
    state["wa_day"] = day
    save_state(state)


def embed_images(html, images):
    """The body with its pictures inside it, for opening in a browser."""
    for cid, png in images.items():
        html = html.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(png).decode())
    return html


def subject(as_of):
    return f"River FOB Daily: {as_of:%a %b} {as_of.day}, {as_of.year}"


# ── Gather and send ───────────────────────────────────────────────────────────────────────────────
def has_futures(commodity, extras):
    return any(v is not None for v in ((extras[0] or {}).get(commodity) or {}).values())


def gather(as_of_iso, dates, today):
    """Everything the email needs, read fresh for the given saved sheet."""
    as_of = dt.date.fromisoformat(as_of_iso)
    snapshot, extras = db.load_snapshot(as_of_iso), db.load_extras(as_of_iso)
    rate = interest_pct(as_of)
    tops = {c: sheet_top(c, as_of_iso, dates, snapshot, extras, rate) for c in COMMODITIES}
    charts = {c: chart_png(c) for c in COMMODITIES}
    moves = {c: movement(c, today) for c in COMMODITIES}
    return {"as_of": as_of, "tops": tops, "charts": charts, "moves": moves}


def bids_current(moves, today_iso):
    return all(moves.get(c) and moves[c]["table"]["latest"] == today_iso for c in COMMODITIES)


def recipients():
    """(to, bcc). The list goes on BCC with Kolten on To, as the Inco email does, so a Reply All can't reach the whole list. No list set = nobody."""
    bcc = CB._split(os.environ.get("RIVER_EMAIL_BCC"))
    to = CB._split(os.environ.get("RIVER_EMAIL_TO")) or ([NOTIFY] if bcc else [])
    return to, bcc


def send(cfg, recips, subj, html, images, bcc=()):
    """Graph sendMail with `images` {cid: png} as inline attachments, from the same mailbox and app as the 5 PM alert."""
    import requests
    atts = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": f"{cid}.png", "contentType": "image/png",
             "contentBytes": base64.b64encode(png).decode(), "isInline": True, "contentId": cid}
            for cid, png in images.items()]
    msg = {"message": {"subject": subj, "body": {"contentType": "HTML", "content": html},
                       "toRecipients": [{"emailAddress": {"address": a}} for a in recips],
                       "bccRecipients": [{"emailAddress": {"address": a}} for a in bcc],
                       "replyTo": [{"emailAddress": {"address": REPLY_TO}}], "attachments": atts},
           "saveToSentItems": False}
    r = requests.post(f"{CB.GRAPH}/users/{quote(cfg['GRAPH_SENDER'])}/sendMail",
                      headers={"Authorization": f"Bearer {CB._token(cfg)}", "Content-Type": "application/json"},
                      data=json.dumps(msg), timeout=90)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"sendMail returned HTTP {r.status_code}: {r.text[:300]}")


def _graph():
    cfg = CB._graph_cfg()
    missing = [k for k in CB.GRAPH_KEYS if not cfg[k]]
    if missing:
        raise RuntimeError(f"Graph not configured, missing: {', '.join(missing)}")
    return cfg


def load_state(path=STATE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state, path=STATE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def decide(sheet_ready, bids_ready, last_run):
    """What a due run does: 'wait', 'send', 'send_stale' (today's sheet, older bids) or 'notify' (the last run found no sheet)."""
    if not sheet_ready:
        return "notify" if last_run else "wait"
    if bids_ready:
        return "send"
    return "send_stale" if last_run else "wait"


def report_failure(state, today_iso, err, last_run):
    """Count a failed run. -> the exit code: quiet (0) until the third failure of the day, then every 12th, and on the last run."""
    n = (state.get("fails", 0) + 1) if state.get("fail_day") == today_iso else 1
    state.update(fail_day=today_iso, fails=n)
    save_state(state)
    log.error("Run failed (%d today): %s", n, err)
    return 3 if (last_run or n == 3 or (n > 3 and (n - 3) % 12 == 0)) else 0


def notify_none(cfg, today, newest):
    day = CB._nice(today)
    html = (f'<div style="{FONT}font-size:14px;line-height:1.5"><p>The River FOB Daily did <b>not</b> go out today ({escape(day)}): the sheet still '
            f"wasn't saved with its futures at the last check, 7:55 PM CT.</p><p>Most recent saved sheet: <b>{escape(newest)}</b>.</p></div>")
    CB._send(cfg, [NOTIFY], f"[River FOB] Daily email not sent ({day})", html)


def preview(path, today):
    dates = db.list_dates()
    d = gather(dates[0], dates, today)
    subj, html, images = compose(d)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!-- Subject: {escape(subj)} -->\n" + embed_images(html, images))
    log.info("Wrote %s (sheet %s; bids as of %s)", path, dates[0],
             {c: (d["moves"][c] or {}).get("table", {}).get("latest") for c in COMMODITIES})


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="The River FOB daily email")
    ap.add_argument("--preview", metavar="FILE", help="write the newest sheet's email to a file; send nothing")
    ap.add_argument("--dry-run", action="store_true", help="say what today's run would do; send nothing")
    ap.add_argument("--to", help="send the newest sheet's email to this address only; no state kept")
    ap.add_argument("--pngs", metavar="DIR", help="write the newest sheet's WhatsApp pictures to a folder; send nothing")
    ap.add_argument("--wa-to", metavar="CHAT_ID", help="post the newest sheet's pictures to this WhatsApp chat only; no state kept")
    a = ap.parse_args(argv)

    if db._backend() == "sqlite":
        log.error("No shared backend (USE_SNOWFLAKE + SNOWFLAKE_*). Refusing to read SQLite.")
        return 3
    now = dt.datetime.now(CB.CT)
    today, today_iso = now.date(), now.date().isoformat()

    if a.preview:
        preview(a.preview, today)
        return 0
    if a.to:
        dates = db.list_dates()
        d = gather(dates[0], dates, today)
        send(_graph(), CB._split(a.to), *compose(d))
        log.info("Test sent to %s (sheet %s)", a.to, dates[0])
        return 0

    if a.pngs or a.wa_to:
        dates = db.list_dates()
        d = gather(dates[0], dates, today)
        if a.pngs:
            os.makedirs(a.pngs, exist_ok=True)
            for name, png in whatsapp_pngs(d):
                with open(os.path.join(a.pngs, name + ".png"), "wb") as f:
                    f.write(png)
            log.info("Wrote the pictures to %s (sheet %s)", a.pngs, dates[0])
        if a.wa_to:
            cfg = {"session": os.environ.get("RIVER_WA_SESSION") or WA.DEFAULT_SESSION, "key": os.environ.get("WA_API_KEY") or ""}
            WA.send(cfg, [a.wa_to], *whatsapp_parts(d))
            log.info("Test posted to one WhatsApp chat (sheet %s)", dates[0])
        return 0

    state = load_state()
    recips, bcc = recipients()
    wa = WA.config()
    trading = today.weekday() < 5 and today_iso not in CB._skip_dates()
    email_due = bool(recips) and state.get("done_day") != today_iso
    wa_due = wa is not None and state.get("wa_day") != today_iso
    last_run = now.time() >= LAST_RUN
    if not trading or not (email_due or wa_due or a.dry_run):
        log.info("Nothing to do: trading day=%s, email due=%s, WhatsApp due=%s", trading, email_due, wa_due)
        return 0
    try:
        dates = [str(d)[:10] for d in db.list_dates()]
        extras = db.load_extras(today_iso) if today_iso in dates else ({}, {})
        sheet_ready = today_iso in dates and all(has_futures(c, extras) for c in COMMODITIES)
        if not sheet_ready:
            action = decide(False, False, last_run)
            log.info("Today's sheet isn't saved with futures yet (newest %s): %s", dates[0] if dates else "none", action)
            if action == "notify" and not a.dry_run:
                notify_none(_graph(), today, dates[0] if dates else "none")
                state.update(done_day=today_iso, wa_day=today_iso)
                save_state(state)
            return 0
        d = gather(today_iso, dates, today)
        action = decide(True, bids_current(d["moves"], today_iso), last_run)
        log.info("Sheet %s saved; bids %s: %s", today_iso, {c: (d["moves"][c] or {}).get("table", {}).get("latest") for c in COMMODITIES}, action)
        if action not in ("send", "send_stale") or a.dry_run:
            return 0
        if email_due:
            send(_graph(), recips, *compose(d), bcc=bcc)
            state.update(done_day=today_iso, sent_at=now.isoformat(timespec="seconds"), fails=0)
            save_state(state)
            log.info("Email sent to %s (bcc %s)", ", ".join(recips), ", ".join(bcc))
        if wa_due:
            whatsapp_once(wa, d, state, today_iso)
            log.info("WhatsApp posted to %d chat(s)", len(wa["chats"]))
        return 0
    except WA.MaybeSent as e:
        log.exception("WhatsApp may have posted in part, so it won't retry today: %s", e)
        return 3
    except Exception as e:
        log.exception("River email run failed")
        return report_failure(state, today_iso, e, last_run)


if __name__ == "__main__":
    sys.exit(main())
