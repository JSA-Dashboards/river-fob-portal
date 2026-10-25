"""🛳 Vessel Lineup, focused for the River FOB sheet: the US Gulf line-up first, then the PNW.

Kolten 2026-10-25: "We are going to be most interested in the USG line up only followed by PNW. Can we focus the filters and views
on these?" USG is the Mississippi River export ports (South Louisiana, New Orleans, Plaquemines, Baton Rouge), where the barges this
sheet prices unload; PNW is the Columbia River and Puget Sound. Texas Gulf, Brazil and Argentina stay on the full Summary page (the
Global Export Dashboard's Vessel Lineup tab and the standalone Vessel Line-up dashboard).

Built only from the mirrored page's own pieces (vessel_mirror/overview.py: the data prep, tiles, change and port tables, sailed
table, line-up and sailed views), so the numbers and the look are the standalone dashboard's; this file only decides what is shown
and in what order. vessel_mirror/ is GENERATED and vessel_tab.py must stay byte-identical to the Global Export Dashboard's copy, so
the focus lives here. Those pieces are the page's internals: after every re-sync of vessel_mirror/, run
`python tests/test_vessel_focus.py`. Widget keys start with `rfv_`, so they never meet the full page's (the admin portal carries
both pages and shares one session state).
"""
import os

import pandas as pd
import streamlit as st

import vessel_tab
from vessel_mirror import copy_tools, data_sync
from vessel_mirror import overview as ov
from vessel_mirror.fact_model import changes as ch

FOCUS = {"USG": "US Gulf (USG)", "PNW": "PNW"}          # area -> heading, in the order shown
SOURCE = "US"
K = "rfv"                                              # widget-key prefix


def scope(area):
    """The selection for one area: the same as the full page's SCOPES entry for it (the test checks they still agree)."""
    return {"source": [SOURCE], "area": [area]}


def _sailed_total(events, measure):
    """The sailed windows of `events` summed over commodities (mtd, d_mo, d_yr, ytd ...), or None with no sailings."""
    if events.empty:
        return None
    w = ch.sailed_windows(events, ["commodity"], measure)
    return None if w.empty else w[ch.WINDOWS + ch.DELTAS].sum(min_count=1)


def _tiles(detail, events, measure):
    """USG and PNW line-up now, then what each has sailed month-to-date."""
    unit = ov._unit(measure)
    cols = st.columns(2 * len(FOCUS))
    for col, area in zip(cols, FOCUS):
        d = ov._apply(detail, scope(area))
        if d.empty:
            col.markdown(ov.tile(f"{area} line-up", "—"), unsafe_allow_html=True)
            continue
        r = ch.rollup(d.assign(_t="t"), ["_t"]).iloc[0]
        col.markdown(ov.tile(f"{area} line-up ({unit})", ov._num(r["now"]), f"{ov._arrow(r['d_wk'])} wk",
                             f"Mo {ov._delta(r['d_mo'])} · Yr {ov._delta(r['d_yr'])}"), unsafe_allow_html=True)
    for col, area in zip(cols[len(FOCUS):], FOCUS):
        s = _sailed_total(ov._apply(events, scope(area)), measure)
        if s is None:
            col.markdown(ov.tile(f"{area} sailed MTD", "—"), unsafe_allow_html=True)
            continue
        col.markdown(ov.tile(f"{area} sailed MTD ({unit})", ov._num(s["mtd"]), f"{ov._arrow(s['d_mo'])} mo",
                             f"Yr {ov._delta(s['d_yr'])} · YTD {ov._num(s['ytd'])}"), unsafe_allow_html=True)


def _snapshot(detail, events, measure):
    """Commodity rows x USG / PNW columns, side by side: line-up now, and sailed month-to-date."""
    unit = ov._unit(measure)
    now = {a: ov._by_commodity(ov._apply(detail, scope(a)), "now") for a in FOCUS}
    mtd = {}
    for a in FOCUS:
        ev = ov._apply(events, scope(a))
        w = ch.sailed_windows(ev, ["commodity"], measure) if not ev.empty else pd.DataFrame()
        mtd[a] = ov._by_commodity(w, "mtd") if not w.empty else pd.Series(0.0, index=ov.COMMODITIES)
    for col, (title, cols, key) in zip(st.columns(2), ((f"Line-up now ({unit})", now, "now"),
                                                       (f"Sailed month-to-date ({unit})", mtd, "mtd"))):
        m = pd.DataFrame(cols)[list(FOCUS)]
        m.loc["Total"] = m.sum()
        out = m.map(lambda v: "" if v == 0 else ov._num(v))
        out.insert(0, "Commodity", out.index)
        with col:
            st.markdown(f"**{title}**")
            copy_tools.table(out, f"{K}_snap_{key}", styler=ov._styled(out, [], "Commodity"), height=38 * (len(out) + 1))


def _terminal_table(d, key):
    """Terminal rows x commodity columns (line-up now), with the total and the week / month / year change: the full page's port
    table, one level down. A terminal with nothing now and nothing a week, month or year ago is left out."""
    keep = d.groupby("elevator")[["now", *ch.PREV]].sum(min_count=1).fillna(0).sum(axis=1) > 0
    d = d[d["elevator"].isin(keep[keep].index)]
    if d.empty:
        st.info("Nothing in the line-up for this selection.")
        return
    t = ov.port_table(d.assign(port=d["elevator"])).drop(columns="Area").rename(columns={"Port": "Terminal"})
    where = d.groupby("elevator")["port"].agg(lambda s: s.value_counts().index[0])
    t.insert(1, "Port", t["Terminal"].map(where).fillna(""))
    copy_tools.table(t, key, styler=ov._styled(t, ["Δ Week", "Δ Month", "Δ Year"]), height=min(38 * (len(t) + 1), 640))


def _area_section(area, detail, events, measure, expanded):
    d, ev = ov._apply(detail, scope(area)), ov._apply(events, scope(area))
    with st.expander(FOCUS[area], expanded=expanded):
        if d.empty and ev.empty:
            st.info(f"Nothing banked for {area} yet.")
            return
        st.markdown("**Line-up by commodity**")
        ov.show_change_table(d, "commodity", "Commodity", f"{K}_ct_{area}")
        st.markdown("**Line-up by terminal**")
        _terminal_table(d, f"{K}_term_{area}")
        if not ev.empty:
            st.markdown("**Sailed**")
            t = ov._sailed_table(ch.sailed_windows(ev, ["commodity"], measure), "Commodity", "commodity")
            copy_tools.table(t, f"{K}_sailed_{area}", styler=ov._styled(t, [c for c in t.columns if c.startswith(("Δ", "%"))], "Commodity"),
                             height=min(38 * (len(t) + 1), 400))


def _explorer(lineup, events, detail, meta, gaps, left_out, measure):
    """The full page's explorer, held to the US and to USG / PNW, starting on USG."""
    st.markdown("### Explore")
    st.caption("Starts on the US Gulf: add the PNW, pick commodities, or narrow to a port or terminal; line-up or sailed.")
    first = next(iter(FOCUS))
    c1, c2, c3 = st.columns([2, 3, 2])
    with c1:
        area = st.pills("Area", list(FOCUS), selection_mode="multi", default=[first], key=f"{K}_ex_area") or [first]
    area = [a for a in FOCUS if a in area]                     # USG before PNW, however they were clicked
    with c2:
        comm = st.multiselect("Commodity", ov.COMMODITIES, key=f"{K}_ex_comm", placeholder="All six commodities")
    with c3:
        view = st.segmented_control("View", ["Line-up", "Sailed"], default="Line-up", key=f"{K}_ex_view") or "Line-up"
    cols = ["source", "area", "port", "elevator"]
    base = pd.concat([detail[cols], events[cols]]).drop_duplicates()
    base = base[(base["source"] == SOURCE) & base["area"].isin(area)]
    # the port and terminal lists depend on the selections above them, so those selections are part of each widget's key
    with st.expander("Narrow to a port or terminal (optional)"):
        k1, k2 = st.columns(2)
        with k1:
            port = st.multiselect("Port", sorted(base["port"].unique()), key=f"{K}_ex_port_{'-'.join(area)}", placeholder="All ports")
        elevs = sorted(base[base["port"].isin(port)]["elevator"].unique() if port else base["elevator"].unique())
        with k2:
            elev = st.multiselect("Elevator / terminal", elevs, key=f"{K}_ex_elev_{'-'.join(area)}_{'-'.join(port)}",
                                  placeholder="All terminals")
    sel = {"source": [SOURCE], "area": area, "port": port or None, "elevator": elev or None, "commodity": comm or ov.COMMODITIES}
    groups = [g for g in ov.GROUPS if g != "Country"]
    group_by = st.segmented_control("Group the table by", groups, default="Commodity", key=f"{K}_ex_group") or "Commodity"
    if view == "Line-up":
        ov.lineup_view(sel, lineup, detail, meta, gaps, left_out, measure, f"{K}_ex", group_by)
    else:
        months = st.slider("Months of shipment history", 3, 36, 12, key=f"{K}_ex_months")
        ov.sailed_view(sel, events, measure, months, f"{K}_ex", group_by)


def page(facts_db):
    mtime = os.path.getmtime(facts_db)
    t1, t2 = st.columns([3, 3], vertical_alignment="bottom")
    with t1:
        measure = ov._measure_toggle(K)
    with t2:
        split, fill = ov._split_toggle(K)
    lineup, events, detail, meta, gaps, left_out = ov._prepare(facts_db, mtime, measure, split, fill)
    if split or fill:
        ov.combo_method_note(facts_db, mtime)
    st.caption(f"In {ov._unit(measure)}. Line-up now, and its change vs last week (wk), month (Mo) and year (Yr); sailed "
               "month-to-date vs the same days last month (mo) and last year (Yr).")
    _tiles(detail, events, measure)
    ov._asof_caption(meta, [SOURCE], left_out)
    st.markdown("##### Snapshot by commodity")
    _snapshot(detail, events, measure)
    st.markdown("##### Week, month and year change")
    for i, area in enumerate(FOCUS):
        _area_section(area, detail, events, measure, expanded=i == 0)
    st.markdown("---")
    _explorer(lineup, events, detail, meta, gaps, left_out, measure)


def render() -> None:
    """Draw the tab: vessel_tab's styling, reload button and data status, then the focused page. The caller draws the title."""
    st.markdown(vessel_tab._css(), unsafe_allow_html=True)
    head_l, head_r = st.columns([5, 2], vertical_alignment="center")
    with head_l:
        st.caption("US Gulf first, then the PNW: the vessel line-up now, and what has sailed, from the daily port reports. "
                   "Texas Gulf, Brazil and Argentina are on the full Vessel Line-up dashboard.")
    with head_r:
        if st.button("Reload data", icon=":material/refresh:", key=f"{K}_reload", width="stretch"):
            vessel_tab._clear_caches()                               # before the data is read below, so one click is enough
    info = vessel_tab._data()
    facts = info["facts.db"]
    with head_r:
        st.caption(data_sync.describe(info))
    if not os.path.exists(facts["path"]):
        st.warning(f"The vessel line-up data could not be reached just now ({facts['error'] or 'file missing'}). "
                   "Click **Reload data** to try again.")
        return
    with st.container(key=vessel_tab._ROOT):
        page(facts["path"])
