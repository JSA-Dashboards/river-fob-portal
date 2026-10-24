"""The Vessel Lineup tab: the Summary page of the Vessel Line-up dashboard, mirrored.

The page itself is vessel_mirror/overview.py, generated from JSA-Dashboards/jsa-vessel-lineup-dashboard by
sync_vessel_summary.py and never edited here. This file is the hand-written rest: it fetches the data, supplies the styling
the page expects from the standalone app's app.py (the tiles and the table watermark, scoped to this tab so nothing else on
the dashboard changes) and draws the reload button and the data-status line.

facts.db is not part of this repo. It is read from the standalone repo's published copy on GitHub (data_sync.fetch_one:
conditional requests, atomic writes, the last good copy kept) and re-checked every five minutes; that repo's droplet job
refreshes it daily. VESSEL_FACTS_DB points the tab at a local facts.db instead (tests, offline work).
"""

import base64
import functools
import os
from pathlib import Path

import streamlit as st

from vessel_mirror import data_sync, overview

_HERE = Path(__file__).resolve().parent
_ROOT = "vessel_summary"                      # key of the container the page is drawn in, so its class is st-key-vessel_summary
_GREEN = "#5e7164"                            # the top rule of a tile (JSA_GREEN in the standalone app)


@functools.lru_cache(maxsize=1)
def _css() -> str:
    """The standalone app's tile and table-watermark styles. The colours come from the mirrored page, so they follow it."""
    r = f".st-key-{_ROOT}"
    wm = ""
    try:
        logo = base64.b64encode((_HERE / "vessel_mirror" / "assets" / "logo-50yr.png").read_bytes()).decode()
        wm = (f'{r} [class*="st-key-tablewrap_"] {{ position: relative; }}\n'
              f'{r} [class*="st-key-tablewrap_"]::after {{ content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 5;\n'
              f"  background-image: url('data:image/png;base64,{logo}'); background-repeat: no-repeat; background-position: center center;\n"
              f"  background-size: min(34%, 260px); opacity: 0.10; }}")
    except OSError:
        pass
    return f"""<style>
{r} .tile {{ background:{overview.SURFACE}; border:1px solid {overview.BORDER}; border-top:3px solid {_GREEN};
    border-radius:10px; padding:14px 16px; text-align:center; height:100%; margin-bottom:8px; }}
{r} .tile-label {{ color:{overview.MUTED}; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.09em; margin-bottom:6px; }}
{r} .tile-value {{ color:{overview.INK}; font-size:1.55rem; font-weight:700; line-height:1.1; }}
{r} .tile-sub {{ color:{overview.MUTED}; font-size:0.72rem; margin-top:5px; }}
{r} .tile-delta-neu {{ color:{overview.MUTED}; font-size:0.82rem; font-weight:600; margin-top:4px; }}
{wm}
</style>"""


@st.cache_data(ttl=300, show_spinner="Loading the vessel line-up…")
def _facts(app_dir: str) -> dict:
    """{'facts.db': {path, source, checked_at, error, ...}}. Cached for five minutes, so an interaction costs at most a
    conditional request, and never a download unless the file changed."""
    return {"facts.db": data_sync.fetch_one("facts.db", app_dir)}


def _data() -> dict:
    local = os.environ.get("VESSEL_FACTS_DB")
    if local:
        return {"facts.db": {"name": "facts.db", "path": local, "source": "local", "fetched_at": None, "checked_at": None, "error": None}}
    return _facts(str(_HERE))


def _clear_caches() -> None:
    """Forget the vessel data only. st.cache_data.clear() would also empty the rest of the dashboard's caches."""
    _facts.clear()
    for obj in vars(overview).values():
        if callable(getattr(obj, "clear", None)) and hasattr(obj, "__wrapped__"):        # the page's @st.cache_data functions
            obj.clear()


def render() -> None:
    """Draw the tab's content: the reload button and data status, then the mirrored Summary page. The caller draws the title."""
    st.markdown(_css(), unsafe_allow_html=True)
    head_l, head_r = st.columns([5, 2], vertical_alignment="center")
    with head_l:
        st.caption("United States · Brazil · Argentina: the vessel line-up now, and what has sailed, from the daily port reports.")
    with head_r:
        if st.button("Reload data", icon=":material/refresh:", key="vessel_reload", width="stretch"):
            _clear_caches()                                              # before the data is read below, so one click is enough
    info = _data()
    facts = info["facts.db"]
    with head_r:
        st.caption(data_sync.describe(info))
    if not os.path.exists(facts["path"]):
        st.warning(f"The vessel line-up data could not be reached just now ({facts['error'] or 'file missing'}). "
                   "Click **Reload data** to try again.")
        return
    with st.container(key=_ROOT):
        overview.page_summary(facts["path"])
