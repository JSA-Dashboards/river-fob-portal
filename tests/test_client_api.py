"""River FOB client API (client_api/) and its key management (api_admin.py, the 🔑 API tab): keys made and revoked in the
tab and checked by the server, the 12-month window, the numbers, the formats, the refusals, the rate limit, the call log
and a Snowflake outage. Synthetic sheets; a throwaway SQLite stands in for RIVER_FOB.API_ADMIN. No Snowflake, no network.

    python tests/test_client_api.py
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp()
os.environ["API_WARM"] = "0"                                        # before the import: the module's own app must not start

import sqlite3                                # noqa: E402

from fastapi.testclient import TestClient    # noqa: E402

import api_admin                              # noqa: E402
import fob_model as M                         # noqa: E402
from client_api import app as A, auth, data   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


TODAY = dt.date(2026, 11, 5)
NEW, MID, OLD = "2026-11-04", "2025-11-06", "2025-11-01"           # OLD is 369 days back: outside the window
CAL = []
for d in (NEW, MID, OLD):
    CAL += [(d, "Corn", 0, "Nov", "CZ"), (d, "Corn", 1, "Dec", "CZ"), (d, "Corn", 2, "Jan", "CH"),
            (d, "Soybeans", 0, "Nov", "SF"), (d, "Soybeans", 1, "Dec", "SF"), (d, "Soybeans", 2, "Jan", "SF"),
            (d, "Wheat", 0, "Nov", "WZ"), (d, "Wheat", 1, "Dec", "WZ"), (d, "Wheat", 2, "Jan", "WH")]
CIF = [(NEW, "Corn", "Nov", 0.93), (NEW, "Corn", "Dec", 1.00), (NEW, "Corn", "Jan", 0.93),
       (NEW, "Soybeans", "Nov", 1.10), (NEW, "Wheat", "Nov", 0.80),
       (MID, "Corn", "Nov", 0.70), (OLD, "Corn", "Nov", 9.99), (NEW, "Sorghum", "Nov", 1.0)]
FRT = [(NEW, "IL", "Nov", 7.25), (NEW, "IL", "Dec", 6.75), (NEW, "IL", "Jan", 6.75), (NEW, "Lower Miss", "Nov", 4.0),
       (NEW, "Upper Miss", "Nov", 0.0),                                 # the upper river closed: no quote
       (MID, "IL", "Nov", 6.0), (OLD, "IL", "Nov", 9.0), (NEW, "Nowhere", "Nov", 5.0)]
ROWS = {"cif": CIF, "freight": FRT, "calendar": CAL}

print("data: what a sheet becomes")
ds = data.build_dataset(CIF, FRT, CAL, TODAY)
check("dates: newest first, the 369-day-old sheet left out", ds["dates"] == [NEW, MID], ds["dates"])
check("no row from outside the window, in any dataset", all(r["date"] != OLD for k in ("cif", "freight", "fob") for r in ds[k]))
check("other commodities and unknown regions are dropped",
      {r["commodity"] for r in ds["cif"]} <= set(data.COMMODITIES) and {r["region"] for r in ds["freight"]} <= set(data.REGIONS))
jan = next(r for r in ds["cif"] if r["date"] == NEW and r["commodity"] == "Corn" and r["month_label"] == "Jan")
check("a month gets its year and its contract: Jan on a Nov 4 2026 sheet = 2027-01 vs ZCH27",
      jan["delivery_month"] == "2027-01" and jan["contract"] == "ZCH27", jan)
soy = next(r for r in ds["cif"] if r["date"] == NEW and r["commodity"] == "Soybeans")
check("soybean Nov quoted off SF = ZSF27", soy["contract"] == "ZSF27", soy)
hen = next(r for r in ds["fob"] if r["date"] == NEW and r["commodity"] == "Corn" and r["location"] == "Hennepin"
           and r["month_label"] == "Nov")
want = round(M.fob_value(0.93, 7.25, 5.07, 56), 4)
check("FOB = the portal's own formula (Hennepin corn Nov: 0.93 - 5.07 x 7.25 / 2000 x 56)", hen["fob"] == want == -0.0992,
      (hen["fob"], want))
check("FOB rows carry river, freight region and contract", hen["river"] == "Illinois River" and hen["freight_region"] == "IL"
      and hen["contract"] == "ZCZ26", hen)
check("a closed reach (freight 0): no freight row and no FOB (Savage)",
      not any(r["region"] == "Upper Miss" for r in ds["freight"]) and not any(r["location"] == "Savage" for r in ds["fob"]))
check("Gulfport (discontinued) is never served", not any(r["location"] == "Gulfport" for r in ds["fob"])
      and "Gulfport" not in [l.name for l in data.LOCATIONS])
check("18 locations, 7 freight regions, as the live sheet", len(data.LOCATIONS) == 18 and len(data.REGIONS) == 7)
frt = next(r for r in ds["freight"] if r["date"] == NEW and r["region"] == "IL" and r["month_label"] == "Nov")
check("freight in percent of tariff (7.25 -> 725)", frt["freight_pct"] == 725.0 and frt["delivery_month"] == "2026-11", frt)
check("no futures prices anywhere (contract names only)",
      not any(k in r for k in ("futures", "price", "settle", "cbot") for kk in ("cif", "fob") for r in ds[kk]))

spot = data.build_dataset([("2025-11-12", "Corn", "Spot", 0.5), ("2025-11-12", "Corn", "Nov", 0.6)],
                          [("2025-11-12", "IL", "Spot", 5.5), ("2025-11-12", "IL", "Nov", 5.0)],
                          [("2025-11-12", "Corn", 0, "Spot", "CZ"), ("2025-11-12", "Corn", 1, "Nov", "CZ")], TODAY)
sf = {r["month_label"]: r for r in spot["freight"]}
check("an older sheet's Spot column = the sheet's own month, flagged spot (freight has no calendar of its own)",
      sf["Spot"]["delivery_month"] == "2025-11" and sf["Spot"]["spot"] and sf["Nov"]["delivery_month"] == "2025-11"
      and not sf["Nov"]["spot"], sf)

print("\nkeys: made in the 🔑 API tab (api_admin), checked by the server (auth.KeyStore)")
# A throwaway SQLite stands in for RIVER_FOB.API_ADMIN (same columns as client_api/sql/setup.sql step 1b).
DB = sqlite3.connect(os.path.join(TMP, "api_admin.db"), check_same_thread=False)
DB.executescript("""
CREATE TABLE api_keys (key_id TEXT PRIMARY KEY, client TEXT NOT NULL, key_hash TEXT NOT NULL, key_hint TEXT, note TEXT,
                       created_at TEXT NOT NULL, created_by TEXT, active BOOLEAN NOT NULL DEFAULT 1, revoked_at TEXT, revoked_by TEXT);
CREATE TABLE api_calls (ts TEXT, key_id TEXT, client TEXT, path TEXT, query TEXT, status INTEGER, rows_n INTEGER, ms INTEGER, ip TEXT);
""")
T = {"keys_table": "api_keys"}
KEY, rec = api_admin.create_key(DB, "?", "Hertshten", "pilot", **T)
OTHER, rec2 = api_admin.create_key(DB, "?", "Other Client", **T)
stored = DB.execute("SELECT * FROM api_keys").fetchall()
check("the table holds the hash and a 9-character hint, never the key", KEY not in str(stored)
      and auth.key_hash(KEY) in str(stored) and rec["key_hint"] == KEY[:9], stored[0])
check("a key is rfob_ + 43 random characters (256 bits)", KEY.startswith("rfob_") and len(KEY) == 48, len(KEY))
check("the tab and the server hash keys the same way", all(api_admin.key_hash(k) == auth.key_hash(k)
                                                             for k in (KEY, OTHER, "rfob_x")))
try:
    api_admin.create_key(DB, "?", "  ", **T)
    check("a key needs a client name", False)
except ValueError:
    check("a key needs a client name", True)

kclock = {"now": 0.0}
key_reads = []


def active_keys():                                                    # what RIVER_FOB.API_ADMIN.ACTIVE_KEYS returns
    key_reads.append(1)
    return [{"key_id": a, "client": b, "key_hash": c}
            for a, b, c in DB.execute("SELECT key_id, client, key_hash FROM api_keys WHERE active").fetchall()]


store = auth.KeyStore(fetch=active_keys, clock=lambda: kclock["now"])
LOG = os.path.join(TMP, "access.log")
fetches = []


def fetch():
    fetches.append(1)
    return ROWS


lim = auth.RateLimiter(per_min=1000, per_day=100000)
CALLS = auth.CallLog(write=lambda rows: None, start=False)
api = TestClient(A.create_app(store=store, loader=data.Loader(fetch=fetch, today=lambda: TODAY), limiter=lim,
                              access_log=LOG, today=lambda: TODAY, calls=CALLS))
H = {"X-API-Key": KEY}
check("health needs no key", api.get("/v1/health").json() == {"status": "ok"})
check("no key -> 401", api.get("/v1/cif").status_code == 401)
check("a wrong key -> 401", api.get("/v1/cif", headers={"X-API-Key": "rfob_" + "x" * 43}).status_code == 401)
check("a key in the URL is not accepted (401)", api.get(f"/v1/cif?key={KEY}").status_code == 401)
check("the right key -> 200", api.get("/v1/cif", headers=H).status_code == 200)
check("both keys work", api.get("/v1/dates", headers={"X-API-Key": OTHER}).status_code == 200)
check("no docs, no OpenAPI page", api.get("/docs").status_code == 404 and api.get("/openapi.json").status_code == 404)
check("one key read served all of that (cached)", len(key_reads) == 1, len(key_reads))
r = api.get("/v1/cif", headers=H)
check("security headers on every answer", r.headers.get("x-content-type-options") == "nosniff"
      and r.headers.get("cache-control") == "no-store")

check("revoke in the tab", api_admin.revoke_key(DB, "?", rec2["key_id"], **T)
      and not api_admin.revoke_key(DB, "?", rec2["key_id"], **T))
kclock["now"] = 30
check("within the first minute the server still has the old list (the revoke lands within ~1 min)",
      api.get("/v1/dates", headers={"X-API-Key": OTHER}).status_code == 200)
kclock["now"] = 90                                                    # past REFRESH_S: answered from cache, re-read behind it
api.get("/v1/dates", headers=H)
for _ in range(100):
    if not store._busy:
        break
    time.sleep(0.01)
check("past a minute, a call triggers a background re-read; after it, the revoked key -> 401",
      api.get("/v1/dates", headers={"X-API-Key": OTHER}).status_code == 401 and len(key_reads) == 2, len(key_reads))
OTHER2, rec3 = api_admin.create_key(DB, "?", "Other Client", "replacement", **T)
kclock["now"] = 90 + 301                                              # a quiet five minutes: re-read BEFORE answering
check("after a quiet spell a new key works on its very first call (re-read before answering)",
      api.get("/v1/dates", headers={"X-API-Key": OTHER2}).status_code == 200)
api_admin.revoke_key(DB, "?", rec3["key_id"], **T)
kclock["now"] += 301
check("... and a key revoked during a quiet spell is refused on its first call", api.get(
    "/v1/dates", headers={"X-API-Key": OTHER2}).status_code == 401)
dead = auth.KeyStore(fetch=active_keys, clock=lambda: kclock["now"])
check("a fresh store reads the keys once", dead.verify(KEY) is not None)
dead.fetch = lambda: (_ for _ in ()).throw(RuntimeError("snowflake down"))
kclock["now"] += 3600
check("Snowflake down: keys keep working from the last list for a while", dead.verify(KEY) is not None)
kclock["now"] += 86400
check("but after a day with no successful re-read, every key is refused (fail closed)", dead.verify(KEY) is None)

print("\napi: data")
j = api.get("/v1/cif", headers=H).json()
check("no dates given -> the newest sheet only", j["start"] == j["end"] == NEW and {x["date"] for x in j["rows"]} == {NEW}, j["start"])
check("units are stated", "$/bu" in j["units"] and j["window"] == {"first": "2025-11-05", "last": "2026-11-05"}, j["window"])
j = api.get("/v1/fob?commodity=corn&location=hennepin,STL&start=2020-01-01", headers=H).json()
check("start before the window is clipped to it; filters any case; a list of locations (STL has no freight here)",
      j["start"] == "2025-11-05" and {x["location"] for x in j["rows"]} == {"Hennepin"}
      and {x["date"] for x in j["rows"]} <= {NEW, MID} and all(x["commodity"] == "Corn" for x in j["rows"]), (j["start"], len(j["rows"])))
check("STL has no FOB where its freight wasn't quoted (only IL / Lower Miss were)", not any(x["location"] == "STL" for x in j["rows"]))
j = api.get("/v1/freight?region=IL&start=2025-11-06&end=2025-11-06", headers=H).json()
check("one day by start = end", [x["date"] for x in j["rows"]] == [MID] and j["rows"][0]["freight_pct"] == 600.0, j["rows"])
r = api.get("/v1/fob?commodity=Corn&format=csv", headers=H)
lines = r.text.strip().split("\n")
check("CSV: text/csv, a file name, the header, booleans as true/false",
      r.headers["content-type"].startswith("text/csv") and "river_fob_fob_" in r.headers.get("content-disposition", "")
      and lines[0] == ",".join(A.FIELDS["fob"]) and ",false," in lines[1], (r.headers.get("content-type"), lines[:2]))
d = api.get("/v1/dates", headers=H).json()
check("/v1/dates lists the window's sheets", d["dates"] == [NEW, MID], d)
loc = api.get("/v1/locations", headers=H).json()
check("/v1/locations: 18 locations with river and region, 7 regions", len(loc["locations"]) == 18 and len(loc["freight_regions"]) == 7)

print("\napi: refusals")
for url, why in (("/v1/cif?comodity=corn", "a misspelled parameter"), ("/v1/cif?commodity=oats", "an unknown commodity"),
                 ("/v1/fob?location=Gulfport", "a location not served"), ("/v1/cif?start=11/04/2026", "a bad date"),
                 ("/v1/cif?start=2026-11-04&end=2026-11-01", "start after end"), ("/v1/cif?format=xml", "an unknown format"),
                 ("/v1/dates?start=2026-01-01", "a parameter /v1/dates doesn't take")):
    r = api.get(url, headers=H)
    check(f"400 for {why}", r.status_code == 400 and "detail" in r.json(), (r.status_code, r.text[:120]))
r = api.get("/v1/cif?start=2024-01-01&end=2024-12-31", headers=H)
check("a range wholly before the window -> 400 (never older data)", r.status_code == 400, r.status_code)
check("one Snowflake read served every call so far (the cache)", len(fetches) == 1, len(fetches))

print("\napi: rate limit")
t = {"now": 0.0}
small = TestClient(A.create_app(store=store, loader=data.Loader(fetch=fetch, today=lambda: TODAY),
                                limiter=auth.RateLimiter(per_min=3, per_day=5, clock=lambda: t["now"]), today=lambda: TODAY))
codes = [small.get("/v1/dates", headers=H).status_code for _ in range(4)]
r = small.get("/v1/dates", headers=H)
check("the 4th call in a minute -> 429 with Retry-After", codes == [200, 200, 200, 429] and int(r.headers["retry-after"]) > 0,
      (codes, r.headers.get("retry-after")))
t["now"] = 61
codes = [small.get("/v1/dates", headers=H).status_code for _ in range(3)]
check("a minute later it's open again, until the day's 5 are used", codes == [200, 200, 429], codes)

print("\napi: a Snowflake outage")
state = {"fail": False, "day": TODAY, "n": 0}


def flaky():
    state["n"] += 1
    if state["fail"]:
        raise RuntimeError("snowflake down")
    return ROWS


ld = data.Loader(fetch=flaky, ttl=0, today=lambda: state["day"])
out = TestClient(A.create_app(store=store, loader=ld, limiter=auth.RateLimiter(10 ** 6, 10 ** 6), today=lambda: state["day"]))
check("first load works", out.get("/v1/dates", headers=H).json()["dates"] == [NEW, MID])
state.update(fail=True, day=dt.date(2026, 11, 7))                   # Nov 7: MID (2025-11-06) is now outside the window


def settle(loader):
    for _ in range(200):
        if not loader._refreshing:
            return
        time.sleep(0.01)


j = out.get("/v1/dates", headers=H).json()
check("Snowflake down: still serving, and the window moved with the day (MID dropped)", j["dates"] == [NEW], j)
settle(ld)
n = state["n"]
for _ in range(3):
    out.get("/v1/dates", headers=H)
settle(ld)
check("and it doesn't hammer Snowflake on every call while it's down (retries after a minute)", state["n"] == n, (n, state["n"]))

slow = {"n": 0}


def slow_fetch():
    slow["n"] += 1
    if slow["n"] > 1:
        time.sleep(1.0)
        return {"cif": CIF[:1], "freight": FRT[:1], "calendar": CAL}  # the re-read finds less: proves it was swapped in
    return ROWS


sl = data.Loader(fetch=slow_fetch, ttl=0, today=lambda: TODAY)
first = sl.get()
t0 = time.monotonic()
again = sl.get()
check("a stale cache answers at once while Snowflake is re-read in the background",
      time.monotonic() - t0 < 0.3 and again is first, round(time.monotonic() - t0, 2))
settle(sl)
check("and the re-read is swapped in when it lands", sl.get() is not first and len(sl._rows["cif"]) == 1)
cold = TestClient(A.create_app(store=store, loader=data.Loader(fetch=flaky, today=lambda: TODAY), today=lambda: TODAY))
r = cold.get("/v1/dates", headers=H)
check("nothing loaded yet and Snowflake down -> 503, no internals", r.status_code == 503 and "snowflake" not in r.text.lower(),
      (r.status_code, r.text))

print("\napi: the access log (on the server)")
recs = [json.loads(x) for x in open(LOG, encoding="utf-8")]
ok = [x for x in recs if x["status"] == 200 and x["path"] == "/v1/fob"]
check("each call logged with key id, client, path, status, rows, ms", ok and ok[0]["key"] == rec["key_id"]
      and ok[0]["client"] == "Hertshten" and isinstance(ok[0]["rows"], int) and "ms" in ok[0], ok[:1])
check("the key is never written to the log, even when a client puts it in the URL",
      KEY not in open(LOG, encoding="utf-8").read() and any(x["query"] == "key=***" for x in recs))
check("our own parameters are logged as sent", any(x["query"] == "commodity=corn&location=hennepin,STL&start=2020-01-01" for x in recs))
check("refused calls are logged too (401 with no key id)", any(x["status"] == 401 and x["key"] is None for x in recs))

print("\ncalls: recorded for the 🔑 API tab")
buffered = list(CALLS._buf)
check("every /v1/ call but health is buffered for Snowflake; scanner paths (/docs, /favicon.ico ...) are not",
      buffered and not any(c["path"] == "/v1/health" or not c["path"].startswith("/v1/") for c in buffered)
      and len(buffered) == sum(1 for x in recs if x["path"].startswith("/v1/") and x["path"] != "/v1/health")
      and any(not x["path"].startswith("/v1/") for x in recs), (len(buffered), len(recs)))
check("with the API_CALLS columns, a UTC timestamp, and never the key", set(buffered[0]) == set(data.CALL_FIELDS)
      and len(buffered[0]["ts"]) == 19 and KEY not in json.dumps(buffered), buffered[0])


def to_sqlite(rows):
    DB.executemany(f"INSERT INTO api_calls ({', '.join(data.CALL_FIELDS)}) VALUES ({', '.join('?' * len(data.CALL_FIELDS))})",
                   [tuple(r[f] for f in data.CALL_FIELDS) for r in rows])
    DB.commit()


CALLS.write = to_sqlite
n = CALLS.flush()
check("a flush writes them all in one batch and empties the buffer", n == len(buffered) and not CALLS._buf
      and DB.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0] == n, n)
check("an empty buffer writes nothing", CALLS.flush() == 0)
bad = auth.CallLog(write=lambda rows: (_ for _ in ()).throw(RuntimeError("down")), start=False)
bad.add({"ts": "x"})
bad.add({"ts": "y"})
check("a failed write keeps the rows for the next try, in order", bad.flush() == 0 and [r["ts"] for r in bad._buf] == ["x", "y"])
cap = auth.CallLog(write=lambda rows: None, max_buffer=3, start=False)
for i in range(5):
    cap.add({"ts": str(i)})
check("past the cap the oldest are dropped and counted", [r["ts"] for r in cap._buf] == ["2", "3", "4"] and cap.dropped == 2)

written = []
sd = auth.CallLog(write=lambda rows: written.extend(rows), start=False)
with TestClient(A.create_app(store=store, loader=data.Loader(fetch=fetch, today=lambda: TODAY), limiter=lim,
                             today=lambda: TODAY, calls=sd)) as tc:       # `with` runs the app's startup and shutdown
    tc.get("/v1/dates", headers=H)
    tc.get("/v1/fob")
    pending = len(sd._buf)
check("a shutdown (restart / deploy) writes the calls still in the buffer (go-live bug: atexit ran too late)",
      pending == 2 and len(written) == 2 and not sd._buf, (pending, len(written)))

C = {"calls_table": "api_calls"}
keys = {k["key_id"]: k for k in api_admin.list_keys(DB, "?", keys_table="api_keys", **C)}
h = keys[rec["key_id"]]
check("the tab's key list: client, hint, status, last call, calls and rows in 30 days",
      h["client"] == "Hertshten" and h["active"] and h["key_hint"] == KEY[:9] and h["last_call"] and h["calls_n"] > 5
      and h["rows_total"] > 0 and not keys[rec2["key_id"]]["active"] and keys[rec2["key_id"]]["revoked_at"], h)
rc = api_admin.recent_calls(DB, "?", client="Hertshten", **C)
check("recent calls, one client's, newest first", rc and all(r["client"] == "Hertshten" for r in rc)
      and rc == sorted(rc, key=lambda r: r["ts"], reverse=True), len(rc))
du = api_admin.daily_usage(DB, "?", **C)
check("daily usage by client, refused calls under '(no valid key)'", {"Hertshten", "(no valid key)"} <= {r["client"] for r in du}, du)

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
