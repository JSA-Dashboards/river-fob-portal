"""The 🔑 API tab (api_tab.py), driven headless with AppTest on a throwaway SQLite standing in for RIVER_FOB.API_ADMIN:
create a key (shown once, stored as a hash), hide it, revoke it (only after the confirm box), and the usage views.

    python tests/test_api_tab.py
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from streamlit.testing.v1 import AppTest     # noqa: E402

import api_admin                              # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


DBF = os.path.join(tempfile.mkdtemp(), "api_admin.db")
db = sqlite3.connect(DBF)
db.executescript("""
CREATE TABLE api_keys (key_id TEXT PRIMARY KEY, client TEXT NOT NULL, key_hash TEXT NOT NULL, key_hint TEXT, note TEXT,
                       created_at TEXT NOT NULL, created_by TEXT, active BOOLEAN NOT NULL DEFAULT 1, revoked_at TEXT, revoked_by TEXT);
CREATE TABLE api_calls (ts TEXT, key_id TEXT, client TEXT, path TEXT, query TEXT, status INTEGER, rows_n INTEGER, ms INTEGER, ip TEXT);
""")
db.commit()
db.close()

SCRIPT = f"""
import sqlite3, sys
sys.path.insert(0, {REPO!r})
import api_tab
api_tab.render(lambda: (sqlite3.connect({DBF!r}), "?"), tables={{"keys_table": "api_keys", "calls_table": "api_calls"}})
"""


def app():
    at = AppTest.from_string(SCRIPT, default_timeout=60)
    at.run()
    return at


def rows(sql):
    c = sqlite3.connect(DBF)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


print("the empty tab")
at = app()
check("no exception", not at.exception, [e.value for e in at.exception])
check("says there are no keys yet", any("No keys yet" in i.value for i in at.info))

print("\ncreate a key")
at.text_input[0].set_value("Hertshten")
at.text_input[1].set_value("pilot; sent via 1Password")
next(b for b in at.button if b.label == "Create key").click().run()
new = at.session_state["api_new_key"]
check("the key is made and shown once, in a code box", new["raw"].startswith("rfob_") and any(new["raw"] in c.value for c in at.code),
      [c.value for c in at.code])
stored = rows("SELECT client, key_hash, key_hint, note, active FROM api_keys")
check("the table holds Hertshten's hash and hint, not the key", len(stored) == 1 and stored[0][0] == "Hertshten"
      and stored[0][1] == api_admin.key_hash(new["raw"]) and new["raw"] not in str(stored) and stored[0][4] == 1, stored)
check("the key list shows it as active", any("Hertshten" in str(df.value.to_dict()) and "Active" in str(df.value.to_dict())
                                             for df in at.dataframe))
next(b for b in at.button if b.label.startswith("I've stored it")).click().run()
check("'I've stored it' hides the key for good", "api_new_key" not in at.session_state
      and not any(new["raw"] in c.value for c in at.code))

print("\nrevoke")
at.text_input[0].set_value("Hertshten")
next(b for b in at.button if b.label == "Create key").click().run()
second = at.session_state["api_new_key"]
rv = next(b for b in at.button if b.label == "Revoke")
check("Revoke is disabled until the confirm box is ticked", rv.disabled)
kid = rows(f"SELECT key_id FROM api_keys WHERE key_hint = '{second['raw'][:9]}'")[0][0]
pick = at.selectbox(key="api_revoke_pick")
target = next(o for o in pick.options if kid in o)
pick.select(next(k for k in api_admin.list_keys(sqlite3.connect(DBF), "?", keys_table="api_keys", calls_table="api_calls")
                 if k["key_id"] == kid)).run()
at.checkbox(key=f"api_revoke_ok_{kid}").check().run()
next(b for b in at.button if b.label == "Revoke").click().run()
state = dict(rows("SELECT key_id, active FROM api_keys"))
check("only the chosen key is revoked", state[kid] == 0 and sum(1 for v in state.values() if v) == 1, state)
check("its row says Revoked", any("Revoked" in str(df.value.to_dict()) for df in at.dataframe))

print("\nusage")
first_id = next(k for k, v in state.items() if v)
now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
c = sqlite3.connect(DBF)
c.executemany("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?,?,?)", [
    ((now - dt.timedelta(days=2)).isoformat(sep=" ", timespec="seconds"), first_id, "Hertshten", "/v1/fob", "commodity=corn", 200, 144, 12, "203.0.113.5"),
    ((now - dt.timedelta(hours=3)).isoformat(sep=" ", timespec="seconds"), first_id, "Hertshten", "/v1/cif", "", 200, 22, 8, "203.0.113.5"),
    ((now - dt.timedelta(hours=1)).isoformat(sep=" ", timespec="seconds"), None, None, "/v1/fob", "key=***", 401, None, 1, "198.51.100.7"),
    ((now - dt.timedelta(days=40)).isoformat(sep=" ", timespec="seconds"), first_id, "Hertshten", "/v1/fob", "", 200, 9, 9, "203.0.113.5")])
c.commit()
c.close()
import api_tab                                # noqa: E402 — the same module the AppTest script imports
api_tab._load.clear()                         # calls written behind the tab's back: drop its 60-second cache
at = app()
keys = at.dataframe[0].value
row = keys[keys["Key ID"] == first_id].iloc[0]
check("calls and rows over 30 days (the 40-day-old call left out)", row["Calls (30 d)"] == 2 and row["Rows served (30 d)"] == 166,
      row.to_dict())
check("the last call is shown in Central time", "202" in str(row["Last call"]) and ("AM" in row["Last call"] or "PM" in row["Last call"]))
check("a chart of calls per day", len(at.get("vega_lite_chart")) == 1)
check("refused calls are counted and explained", any("no valid key" in cpt.value for cpt in at.caption))
calls = at.dataframe[1].value
check("the recent calls, newest first, refused ones as '(no valid key)'", list(calls["Status"]) == [401, 200, 200]
      and calls["Client"].iloc[0] == "(no valid key)" and calls["Parameters"].iloc[0] == "key=***", calls.to_dict("list"))
at.session_state["api_days"] = 90                # the Period control (AppTest has no handle for segmented_control)
at.run()
row = at.dataframe[0].value.iloc[-1] if len(at.dataframe[0].value) == 1 else at.dataframe[0].value.set_index("Key ID").loc[first_id]
check("90 days takes in the older call", int(row["Calls (90 d)"]) == 3, row.to_dict())
at.selectbox(key="api_client").select("Hertshten").run()
check("filtering to a client hides the refused calls", "(no valid key)" not in at.dataframe[1].value["Client"].tolist())
check("no exception anywhere", not at.exception, [e.value for e in at.exception])

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
