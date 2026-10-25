"""🛳 Vessel Lineup, focused (vessel_focus.py): USG first, then PNW, and nothing else. Run it after every re-sync of vessel_mirror/,
because the page is built from the mirror's internals.

    python tests/test_vessel_focus.py

Uses VESSEL_FACTS_DB when set, else the tab's own GitHub copy of facts.db (needs the network once; cached in the temp dir).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from streamlit.testing.v1 import AppTest      # noqa: E402

import vessel_focus as vf                      # noqa: E402
from vessel_mirror import data_sync            # noqa: E402
from vessel_mirror import overview as ov       # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


print("the full page's scopes")
full = dict(ov.SCOPES)
for area in vf.FOCUS:
    check(f"{area} selects what the full page's {area} does", full.get(area) == vf.scope(area), (full.get(area), vf.scope(area)))

if not os.environ.get("VESSEL_FACTS_DB"):
    info = data_sync.fetch_one("facts.db", REPO)
    if not os.path.exists(info["path"]):
        sys.exit(f"no facts.db: {info['error']}")
    os.environ["VESSEL_FACTS_DB"] = info["path"]

SCRIPT = f"import sys\nsys.path.insert(0, {REPO!r})\nimport vessel_focus\nvessel_focus.render()\n"


def run(**state):
    at = AppTest.from_string(SCRIPT, default_timeout=240)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def tiles(at):
    """The page's own tile row (the combo-method expander above it draws tiles of its own)."""
    labels = [m.value.split('tile-label">')[1].split("<")[0] for m in at.markdown if 'class="tile"' in m.value]
    start = next((i for i, x in enumerate(labels) if x.startswith("USG line-up")), len(labels))
    return labels[start:start + 2 * len(vf.FOCUS)]


print("default view")
at = run()
check("no exception", not at.exception, [e.value for e in at.exception])
check("no error box", not at.error, [e.value for e in at.error])
t = tiles(at)
check("tiles: USG and PNW line-up, then sailed MTD", [x.split(" (")[0] for x in t] ==
      ["USG line-up", "PNW line-up", "USG sailed MTD", "PNW sailed MTD"], t)
everything = " ".join(m.value for m in at.markdown if 'class="tile"' in m.value)
check("no Texas Gulf / Brazil / Argentina tile", not any(f'tile-label">{w}' in everything for w in ("TXG", "Brazil", "Argentina", "Total", "US<")))
frames = [df.value for df in at.dataframe]
snaps = [f for f in frames if list(f.columns) == ["Commodity", "USG", "PNW"]]
check("both snapshot tables are Commodity / USG / PNW", len(snaps) == 2, [list(f.columns) for f in frames[:4]])
check("snapshot ends in a Total row", all(f["Commodity"].iloc[-1] == "Total" for f in snaps))
exp = {e.label: e.proto.expanded for e in at.expander}
check("USG section open, PNW closed", exp.get(vf.FOCUS["USG"]) is True and exp.get(vf.FOCUS["PNW"]) is False, exp)
terms = [f for f in frames if "Terminal" in f.columns]
check("a terminal table per area", len(terms) == 2, len(terms))
check("terminal table has Port and the changes", bool(terms) and {"Port", "Total", "Δ Week"} <= set(terms[0].columns),
      terms[0].columns.tolist() if terms else None)
caps = " ".join(c.value for c in at.caption)
check("as-of caption is the US only", "United States" in caps and "Brazil" not in caps.split("Line-up as of:")[-1][:80], caps[:300])

print("explorer")
check("explorer starts on USG only", at.session_state[f"{vf.K}_ex_area"] == ["USG"], at.session_state[f"{vf.K}_ex_area"])
at2 = run(**{f"{vf.K}_ex_area": ["PNW", "USG"], f"{vf.K}_ex_view": "Sailed"})
check("USG + PNW, sailed view: no exception", not at2.exception, [e.value for e in at2.exception])
check("sailed view draws its charts", len(at2.get("plotly_chart")) >= 2, len(at2.get("plotly_chart")))
at3 = run(**{f"{vf.K}_measure": "Volume (TMT)", f"{vf.K}_ex_group": "Elevator / terminal"})
check("volume, grouped by terminal: no exception", not at3.exception, [e.value for e in at3.exception])
check("volume tiles say TMT", all("TMT" in x for x in tiles(at3)[:4]), tiles(at3)[:4])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all passed")
