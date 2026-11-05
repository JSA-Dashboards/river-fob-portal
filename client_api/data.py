"""What the client API serves: CIF NOLA, barge freight and FOB barge values from the River FOB archive, for the last
12 months of SAVED sheets (never the working sheet), corn / soybeans / wheat.

Kolten 2026-11-05: "River FOB values for a pilot ... one group (client) for now with the key ... the CIF, Barge Freight, and
FOB values for corn, soybeans & Wheat for the past year as the baseline." Chose a rolling 12 months.

Where the numbers come from:
  * The API's own Snowflake user reads three SECURE VIEWS in RIVER_FOB.CLIENT_API (client_api/sql/setup.sql):
    the archive's cif / freight / calendar rows, already cut to the last ~12 months and to the three commodities.
    It cannot see anything else (no futures prices, no other schema), whatever this code does.
  * FOB is computed here exactly as the portal computes it (fob_model.compute_fob_grid:
    FOB = CIF - tariff factor x freight / 2000 x bushel weight), for the 18 locations the live sheet shows
    (fob_model.BLOCK_LAYOUT; Gulfport, discontinued, is left out).
  * A sheet column is a month label with no year ('Dec', 'June'). Each one is placed in time with the portal's own rule
    (net_carry_data.place_columns: the first month starts at the sheet's date, every later column is the next such month)
    and named with its futures contract ('ZCZ26'). A contract NAME only: no futures prices are served.
  * A freight of 0 or blank means the river is closed at that reach: no freight row and no FOB for that month.

Pure functions plus one Snowflake loader with a cache; the tests feed `build_dataset` rows directly.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))                    # the repo root: fob_model, net_carry_data

import fob_model as M                                        # noqa: E402
import net_carry_data as NCD                                 # noqa: E402

COMMODITIES = ("Corn", "Soybeans", "Wheat")
WINDOW_DAYS = 365                                            # rolling 12 months, counted in Central time
CENTRAL = ZoneInfo("America/Chicago")

_BY_NAME = {l.name: l for l in M.LOCATIONS}
LOCATIONS = [_BY_NAME[n] for kind, n, *_ in M.BLOCK_LAYOUT if kind == "fob"]     # the live sheet's 18, in its order
REGIONS = list(dict.fromkeys(r for kind, r, *_ in M.BLOCK_LAYOUT if kind == "freight"))


def today_central() -> dt.date:
    return dt.datetime.now(CENTRAL).date()


def window(today: dt.date) -> tuple[dt.date, dt.date]:
    """(first, last) day a client may see: the last WINDOW_DAYS days through today."""
    return today - dt.timedelta(days=WINDOW_DAYS), today


def _d(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f                              # NaN -> None


def build_dataset(cif_rows, freight_rows, calendar_rows, today: dt.date) -> dict:
    """Rows as the archive stores them -> what the API serves.

    cif_rows      [(as_of, commodity, month_label, $/bu)]
    freight_rows  [(as_of, region, month_label, multiple of tariff: 7.25 = 725%)]
    calendar_rows [(as_of, commodity, seq, month_label, contract_code)]
    Rows outside the window, or for other commodities / regions, are dropped here too (the views already do).
    Returns {'dates': [newest first], 'cif': [...], 'freight': [...], 'fob': [...]}, each row a dict, sorted by
    date, then commodity / region / location, then delivery month."""
    lo, hi = window(today)
    keep = lambda d: lo <= d <= hi                            # noqa: E731
    cif, frt, cal = {}, {}, {}
    for a, c, m, v in cif_rows:
        d, v = _d(a), _num(v)
        if keep(d) and c in COMMODITIES and v is not None:
            cif.setdefault(d, {}).setdefault(c, {})[str(m)] = v
    for a, r, m, v in freight_rows:
        d, v = _d(a), _num(v)
        if keep(d) and r in REGIONS and v:                    # 0 / blank = closed: no quote
            frt.setdefault(d, {}).setdefault(r, {})[str(m)] = v
    for a, c, seq, m, code in calendar_rows:
        d = _d(a)
        if keep(d) and c in COMMODITIES:
            cal.setdefault(d, {}).setdefault(c, []).append((int(seq), str(m), str(code or "")))

    out = {"dates": [], "cif": [], "freight": [], "fob": []}
    for d in sorted(set(cif) | set(frt)):
        placed_by = {}                                        # commodity -> {label: Column}
        month_ym = {}                                         # calendar month -> (year, month), for freight labels
        for c, cols in (cal.get(d) or {}).items():
            cols = [(m, code) for _s, m, code in sorted(cols)]
            placed, _notes, problem = NCD.place_columns(c, d, cols)
            if problem is None:
                placed_by[c] = {p.label: p for p in placed}
                for p in placed:
                    if not p.spot:
                        month_ym.setdefault(p.ym[1], p.ym)

        def where(c, label):
            p = (placed_by.get(c) or {}).get(label)
            if p is not None:
                return f"{p.ym[0]}-{p.ym[1]:02d}", p.spot, p.symbol
            if NCD.is_spot(label):                            # older sheets' prompt column: the sheet's own month
                return f"{d.year}-{d.month:02d}", True, None
            ym = month_ym.get(NCD.month_of(label))
            return (f"{ym[0]}-{ym[1]:02d}" if ym else None), False, None

        def order(label, c=None):
            dm, spot, _ = where(c, label)
            return (dm or "9999", not spot)

        for c in COMMODITIES:
            for label in sorted((cif.get(d) or {}).get(c, {}), key=lambda l: order(l, c)):
                dm, spot, sym = where(c, label)
                out["cif"].append({"date": d.isoformat(), "commodity": c, "delivery_month": dm, "month_label": label,
                                   "spot": spot, "contract": sym, "cif": round(cif[d][c][label], 4)})
        for r in REGIONS:
            for label in sorted((frt.get(d) or {}).get(r, {}), key=order):
                dm, spot, _ = where(None, label)
                out["freight"].append({"date": d.isoformat(), "region": r, "delivery_month": dm, "month_label": label,
                                       "spot": spot, "freight_pct": round(frt[d][r][label] * 100, 2)})
        for c in COMMODITIES:
            row = (cif.get(d) or {}).get(c)
            if not row:
                continue
            labels = sorted(row, key=lambda l: order(l, c))
            grid = M.compute_fob_grid(c, row, frt.get(d) or {}, labels)
            for loc in LOCATIONS:
                for label in labels:
                    v = grid.get(loc.name, {}).get(label)
                    if v is None:
                        continue
                    dm, spot, sym = where(c, label)
                    out["fob"].append({"date": d.isoformat(), "commodity": c, "location": loc.name, "river": loc.reach,
                                       "freight_region": loc.region, "delivery_month": dm, "month_label": label,
                                       "spot": spot, "contract": sym, "fob": round(v, 4)})
        out["dates"].append(d.isoformat())
    out["dates"].sort(reverse=True)
    return out


# ── Snowflake (the API's own read-only user) ──────────────────────────────────────────────────────
VIEWS = {"cif": "SELECT as_of, commodity, month, value FROM CIF_12M",
         "freight": "SELECT as_of, region, month, value FROM FREIGHT_12M",
         "calendar": "SELECT as_of, commodity, seq, month, contract FROM CALENDAR_12M"}


def _private_key():
    from cryptography.hazmat.primitives import serialization
    path = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
    pwd = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(open(path, "rb").read(), password=pwd.encode() if pwd else None)
    return key.private_bytes(encoding=serialization.Encoding.DER, format=serialization.PrivateFormat.PKCS8,
                             encryption_algorithm=serialization.NoEncryption())


def _connect():
    """The API's own Snowflake login. Key-pair only (its user has no password), and the network policy lets it sign in
    from the API server's address only."""
    import snowflake.connector as sc
    return sc.connect(account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
                      role=os.environ.get("SNOWFLAKE_ROLE", "RIVER_FOB_API_ROLE"),
                      warehouse=os.environ["SNOWFLAKE_WAREHOUSE"], database="RIVER_FOB", schema="CLIENT_API",
                      private_key=_private_key(), login_timeout=30, network_timeout=60)


def fetch_rows() -> dict:
    """{'cif': [...], 'freight': [...], 'calendar': [...]} from the CLIENT_API views."""
    conn = _connect()
    try:
        cur = conn.cursor()
        out = {}
        for k, sql in VIEWS.items():
            cur.execute(sql)
            out[k] = cur.fetchall()
        return out
    finally:
        conn.close()


def fetch_keys() -> list[dict]:
    """The active keys (id, client, hash) from RIVER_FOB.API_ADMIN.ACTIVE_KEYS: the same query every time, so Snowflake's
    result cache answers it without a warehouse unless a key changed."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT key_id, client, key_hash FROM RIVER_FOB.API_ADMIN.ACTIVE_KEYS")
        return [{"key_id": a, "client": b, "key_hash": c} for a, b, c in cur.fetchall()]
    finally:
        conn.close()


CALL_FIELDS = ("ts", "key_id", "client", "path", "query", "status", "rows_n", "ms", "ip")


def write_calls(rows: list[dict]):
    """Append call records to RIVER_FOB.API_ADMIN.API_CALLS in one batch (the API's role may only INSERT there)."""
    conn = _connect()
    try:
        conn.cursor().executemany(
            f"INSERT INTO RIVER_FOB.API_ADMIN.API_CALLS ({', '.join(CALL_FIELDS)}) VALUES ({', '.join(['%s'] * len(CALL_FIELDS))})",
            [tuple(r.get(f) for f in CALL_FIELDS) for r in rows])
        conn.commit()
    finally:
        conn.close()


class Loader:
    """The dataset, re-read from Snowflake at most every `ttl` seconds (a day's sheet lands once, mid-afternoon), so
    clients calling all day cost a handful of queries.

    Only the very first load makes a caller wait (~5-20 s: sign-in, warehouse resume, three queries; the server warms
    it at start-up). After that a stale cache is served AT ONCE while one background thread re-reads Snowflake. A
    failed re-read keeps the last rows and tries again a minute later. A new day re-applies the window to the rows in
    hand straight away, so even with Snowflake down no client ever sees a day outside the 12 months."""

    def __init__(self, fetch=fetch_rows, ttl: int = 900, today=today_central, retry: int = 60):
        self.fetch, self.ttl, self.today, self.retry = fetch, ttl, today, retry
        self._rows, self._data, self._at, self._day = None, None, 0.0, None
        self._refreshing = False
        self._lock = threading.Lock()
        self._first = threading.Lock()                       # one blocking first load at a time

    def _build(self, day):
        self._data = build_dataset(self._rows["cif"], self._rows["freight"], self._rows["calendar"], day)
        self._day = day

    def _refresh(self):
        try:
            rows = self.fetch()
        except Exception:                                    # noqa: BLE001 — keep serving what we have
            with self._lock:
                self._at = time.monotonic() - self.ttl + self.retry
                self._refreshing = False
            return
        with self._lock:
            self._rows, self._at = rows, time.monotonic()
            self._build(self.today())
            self._refreshing = False

    def get(self) -> dict:
        with self._lock:
            have = self._rows is not None
        if not have:                                         # first load: the caller waits (and sees the error if it fails)
            with self._first:
                with self._lock:
                    have = self._rows is not None
                if not have:
                    rows = self.fetch()
                    with self._lock:
                        self._rows, self._at = rows, time.monotonic()
                        self._build(self.today())
        with self._lock:
            day = self.today()
            if day != self._day:
                self._build(day)                             # the window moves at midnight, Snowflake or not
            if time.monotonic() - self._at > self.ttl and not self._refreshing:
                self._refreshing = True
                threading.Thread(target=self._refresh, daemon=True, name="river-fob-api-refresh").start()
            return self._data

    def warm(self):
        """Load in the background now (server start-up), so the first client call doesn't wait."""
        threading.Thread(target=lambda: self._safe_get(), daemon=True, name="river-fob-api-warm").start()

    def _safe_get(self):
        import logging
        log = logging.getLogger("river_fob_api")
        try:
            ds = self.get()
            log.warning("Data loaded: %d sheets (%s .. %s), %d FOB rows", len(ds["dates"]),
                        ds["dates"][-1] if ds["dates"] else "-", ds["dates"][0] if ds["dates"] else "-", len(ds["fob"]))
        except Exception:                                    # noqa: BLE001 — the first client call will retry and report
            log.exception("Start-up data load failed; the first call will try again")
