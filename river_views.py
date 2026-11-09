"""The River FOB views that both the app's tabs and the daily email (river_email.py) draw, in one place so the two can't disagree:
the Cash vs Delivery series and chart (💵 Cash vs Del) and the Movement by segment table (🛥 River Bids). No Streamlit here: the
droplet job that sends the email runs without a Streamlit session.
"""
from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd

import db
import delivery_period as DP
import fob_model as M
import river_segments as RS

CASHDEL_PALETTE = ["#a52714", "#e8710a", "#2e8bc0", "#1f5fa8",
                   "#2e7d32", "#7b3fa0", "#b8860b", "#c0392b"]
CHART_LABEL = {"Corn": "Corn", "Soybeans": "Beans", "Wheat": "SRW"}


def contract_order(ct):
    """Sort key ordering a contract code within a summer-starting crop window
    (Jul, Aug, Sep, … then Jan, Feb, Mar of the next year)."""
    n = M.CONTRACT_MONTH.get(str(ct)[-1].upper())
    return 99 if n is None else (n if n >= 7 else n + 12)


# ── Cash vs Delivery ──────────────────────────────────────────────────────────────────────────────
def cashdel_frame(commodity, cash_c):
    """One row per (archived date, active delivery contract): the Cash-vs-
    Delivery basis (¢/bu) at the commodity's cash location, taken at each
    contract's OWN delivery month (CU→Sep, CZ→Dec, CH→Mar), falling back to the
    first window month that uses the contract when its delivery month isn't in
    the window."""
    cif, frt, cal = db.fetch_all()
    loc = M.CARRY_CONFIG[commodity]["cash_loc"]
    rows = []
    for d, by_comm in cif.items():
        cmcif = by_comm.get(commodity)
        cols = (cal.get(d, {}) or {}).get(commodity)
        if not cmcif or not cols:
            continue
        months = [m for m, _ in cols]
        grid = M.compute_fob_grid(commodity, cmcif, frt.get(d, {}), months)
        if loc not in grid:
            continue
        cvd = dict(zip(months,
                       M.cash_vs_delivery(commodity, grid[loc], cash_c, months)))
        # window month label by calendar month number, so a contract can be read
        # at its delivery month rather than the first column that happens to use it.
        by_mn = {}
        for mm, _ct in cols:
            n = M.label_month_num(mm)
            if n is not None:
                by_mn[n] = mm
        seen = set()
        for m, ct in cols:
            if ct in seen:
                continue
            seen.add(ct)
            deliv_mn = M.CONTRACT_MONTH.get(str(ct)[-1].upper())
            ref_m = by_mn.get(deliv_mn, m)      # contract delivery month, else first-seen
            v = cvd.get(ref_m)
            if v is not None:
                rows.append({"date": dt.date.fromisoformat(d), "contract": ct,
                             "cents": round(float(v) * 100, 1)})
    return pd.DataFrame(rows)


def cashdel_window(df, weeks, end):
    """(the rows of the last `weeks` weekly points ending at/before `end`, those dates). Weekly = the most recent day of each ISO
    week. The dates are [] when nothing falls in the window."""
    df = df.assign(wk=df["date"].map(lambda d: d.isocalendar()[:2]))
    weekly = df[df["date"] <= end].groupby("wk")["date"].max().sort_values().tolist()
    keep = weekly[-weeks:]
    return df[df["date"].isin(keep)].drop(columns="wk"), keep


def year_span(keep):
    return f"{keep[0]:%Y}" if keep[0].year == keep[-1].year else f"{keep[0]:%Y}–{keep[-1]:%Y}"


def cashdel_chart(df, keep, commodity, loc, width=None, background="transparent"):
    """The Cash vs Delivery chart for the window from cashdel_window. `width` None fits the page's column; the email renders it
    to a PNG at a fixed width on white."""
    order = sorted(df["contract"].unique(), key=contract_order)
    color = alt.Color("contract:N", sort=order, title=None,
                      scale=alt.Scale(domain=order,
                                      range=CASHDEL_PALETTE[:len(order)]),
                      legend=alt.Legend(orient="top", labelFontWeight="bold"))
    base = alt.Chart(df).encode(
        x=alt.X("date:T", title=None,
                axis=alt.Axis(format="%-d-%b", labelColor="#333",
                              labelFontWeight="bold", labelAngle=0,
                              tickCount=len(keep))),
        y=alt.Y("cents:Q", title=None,
                axis=alt.Axis(labelColor="#333", labelFontWeight="bold")),
        color=color)
    line = base.mark_line(strokeWidth=2.5,
                          point=alt.OverlayMarkDef(size=38, filled=True))
    labels = base.mark_text(dy=-9, fontSize=10, fontWeight="bold").encode(
        text=alt.Text("cents:Q", format=".0f"))
    size = {} if width is None else {"width": width}
    return alt.layer(line, labels).properties(
        height=440, background=background,
        padding={"left": 6, "right": 40, "top": 6, "bottom": 6},
        autosize=alt.AutoSizeParams(type="fit", contains="padding"),
        title=alt.TitleParams(
            f"Cash vs. Delivery: {CHART_LABEL[commodity]} ({loc}) · {year_span(keep)}",
            color="#2e7d32", fontSize=18, fontWeight="bold", anchor="middle"),
        **size
    ).configure_view(strokeWidth=0, fill=None).configure_axis(
        grid=True, gridColor="#ececec", domainColor="#cccccc"
    ).configure_legend(labelColor="#333", symbolStrokeWidth=3, labelFontSize=12)


# ── River bids ────────────────────────────────────────────────────────────────────────────────────
def decorate(frame):
    """Add canonical delivery month (dkey / deliv) and river segment columns.

    Each provider writes the delivery window differently ("July '26",
    "Dec '26 River Close"), so delivery_period resolves it to a real month,
    falling back to the futures contract when the text omits the year.
    """
    # Tolerate rows without delivery_month (e.g. a cached result from before the
    # column was selected) — canonical() then falls back to the futures contract.
    dm_col = (frame["delivery_month"] if "delivery_month" in frame.columns
              else [""] * len(frame))
    keys, labels = [], []
    for dm, fs in zip(dm_col, frame["futures_symbol"]):
        ym = DP.canonical(dm, fs)
        keys.append(ym[0] * 100 + ym[1] if ym else None)
        labels.append(DP.label(ym) if ym else "")
    frame["dkey"] = keys
    frame["deliv"] = labels
    frame["segment"] = [RS.river_segment(loc) for loc in frame["location"]]


def movement_table(hd, sel, segs):
    """Movement for one delivery period `sel`: each segment's median with the terminals it averages listed directly beneath it.
    `hd` = bid history with date / deliv / segment columns; `segs` = the segments to show, in order.

    -> {'rows': [{Segment, Terminal, 'Current ¢', 'Day Δ', 'Week Δ', 'Month Δ'}] ('Segment' is blank on a terminal's row),
        'latest': the newest date, 'bases': [(label, baseline date or None)]}, or None with no bids for it."""
    sub = hd[(hd["deliv"] == sel) & (hd["segment"].isin(segs))]
    if sub.empty:
        return None
    seg_day = sub.groupby(["date", "segment"])["basis_cents"].median().reset_index()
    loc_day = (sub.groupby(["date", "segment", "provider", "location"])
                  ["basis_cents"].median().reset_index())
    dts = sorted(sub["date"].unique())
    latest = dts[-1]
    L = dt.date.fromisoformat(latest)

    def _baseline(back):
        """Last quoted day on/before `back` days ago — gaps don't skew it."""
        elig = [x for x in dts if x <= (L - dt.timedelta(days=back)).isoformat()]
        return elig[-1] if elig else None

    bases = [(lbl, _baseline(n))
             for lbl, n in (("Day", 1), ("Week", 7), ("Month", 30))]

    def _movement(frame, keys):
        cur = frame[frame["date"] == latest].set_index(keys)["basis_cents"]
        out = pd.DataFrame({"Current ¢": cur})
        for lbl, b in bases:
            if b:
                past = frame[frame["date"] == b].set_index(keys)["basis_cents"]
                out[f"{lbl} Δ"] = cur - past
        return out

    seg_mv = _movement(seg_day, ["segment"])
    loc_mv = _movement(loc_day, ["segment", "provider", "location"])
    val_cols = list(seg_mv.columns)

    # One table: each segment's median, with the terminals it averages listed
    # directly beneath it.
    rows = []
    for s in segs:
        kids = (loc_mv[loc_mv.index.get_level_values("segment") == s]
                if len(loc_mv) else loc_mv)
        if s in seg_mv.index:
            r = seg_mv.loc[s]
            rows.append({"Segment": s,
                         "Terminal": f"▸ {s} — segment median ({len(kids)})",
                         **{c: r.get(c) for c in val_cols}})
        for idx, rr in kids.iterrows():
            rows.append({"Segment": "", "Terminal": f"      {idx[1]} · {idx[2]}",
                         **{c: rr.get(c) for c in val_cols}})
    return {"rows": rows, "latest": latest, "bases": bases}


def movement_note(mv):
    """The caption under a movement table (after its delivery period)."""
    note = " · ".join(f"{l.lower()} vs {b}" for l, b in mv["bases"] if b)
    return (f"as of {mv['latest']}" + (f" · {note}" if note else "")
            + " · each segment row is the median of the terminals listed "
              "beneath it · Δ in ¢ (positive = basis firmed).")
