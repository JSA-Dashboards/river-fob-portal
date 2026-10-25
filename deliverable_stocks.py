"""Deliverable Stocks — CBOT corn and soybean stocks in deliverable positions ("visible stocks in delivery space"), weekly.

Kolten 2026-10-21: "Can we add these as a tab and update them as they do on CME website? Make the look similar ... Call it Deliverable
Stocks ... put it on the cash vs DVE tab ... below the cash vs DVE chart."

Source: CME's weekly "Stocks of Grain" report (as of FRIDAY, published the following TUESDAY by noon CT). CME's Data Terms of Use bar
scripts that retrieve or harvest cmegroup.com and compiling or redistributing its data without written consent, so NOTHING here
downloads it: the weekly totals are typed in on the Cash vs Del tab (editors only) until CME licenses its Stocks of Grain DataMine
dataset to JSA, when a DataMine job can write the same table. History before that comes from the old FOB Sheet workbook's
'Deliverable Stox' tab (2020 on), loaded once by `import_deliverable_stocks.py`.

Storage: `deliverable_stocks` in the portal's own database (RIVER_FOB.PUBLIC on Snowflake), one row per (as_of Friday, commodity):
stocks in THOUSAND bushels, the source ('workbook' | 'manual' | 'datamine'). Pure helpers for the charts are below the storage.
"""
from __future__ import annotations

import datetime as dt

import db

COMMODITIES = ("Corn", "Soybeans")
TABLE = "deliverable_stocks"


# ---------------------------------------------------------------- storage -----
def init_table():
    conn, _ = db._connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                as_of TEXT NOT NULL,
                commodity TEXT NOT NULL,
                stocks DOUBLE PRECISION,
                source TEXT,
                PRIMARY KEY (as_of, commodity)
            )""")
        conn.commit()
    finally:
        conn.close()


def save(rows):
    """Upsert [(as_of_iso, commodity, stocks_thousand_bu, source)] -> count written. A week typed again replaces the earlier entry."""
    data = [(str(a)[:10], c, float(v), s) for a, c, v, s in rows if v is not None and c in COMMODITIES]
    if not data:
        return 0
    init_table()
    conn, ph = db._connect()
    try:
        cur = conn.cursor()
        backend = db._backend()
        if backend == "snowflake":
            for i in range(0, len(data), 400):
                part = data[i:i + 400]
                src = ",".join([f"({ph},{ph},{ph},{ph})"] * len(part))
                cur.execute(
                    f"MERGE INTO {TABLE} t USING ("
                    "SELECT column1::string AS as_of, column2::string AS commodity, column3::float AS stocks, "
                    "column4::string AS source FROM VALUES " + src + ") s "
                    "ON t.as_of = s.as_of AND t.commodity = s.commodity "
                    "WHEN MATCHED THEN UPDATE SET stocks = s.stocks, source = s.source "
                    "WHEN NOT MATCHED THEN INSERT (as_of, commodity, stocks, source) "
                    "VALUES (s.as_of, s.commodity, s.stocks, s.source)",
                    [x for r in part for x in r])
        else:                                                      # postgres / sqlite
            cur.executemany(
                f"INSERT INTO {TABLE} (as_of, commodity, stocks, source) VALUES ({ph},{ph},{ph},{ph}) "
                "ON CONFLICT (as_of, commodity) DO UPDATE SET stocks = excluded.stocks, source = excluded.source", data)
        conn.commit()
        return len(data)
    finally:
        conn.close()


def load_all() -> dict:
    """{commodity: {as_of date: stocks (thousand bu)}}; {} when the table does not exist yet."""
    conn, _ = db._connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT as_of, commodity, stocks FROM {TABLE}")
        except Exception:                                          # noqa: BLE001 — no table yet: nothing entered
            return {}
        out: dict = {}
        for a, c, v in cur.fetchall():
            if v is not None:
                out.setdefault(c, {})[dt.date.fromisoformat(str(a)[:10])] = float(v)
        return out
    finally:
        conn.close()


# --------------------------------------------------------------- the charts ---
REF_YEAR = 2000          # a leap year: every as-of date maps onto one Jan-Dec axis


def last_friday(d: dt.date) -> dt.date:
    """The Friday on or before `d`."""
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def newest_report_friday(today: dt.date) -> dt.date:
    """The as-of Friday of the newest Stocks of Grain report that can be out by `today`: CME publishes Friday's stocks the next
    Tuesday, so it is the Friday on or before today - 4 days (Tue Oct 27 -> Fri Oct 23; Mon Oct 26 -> Fri Oct 16)."""
    return last_friday(today - dt.timedelta(days=4))


def missing_fridays(data: dict, upto: dt.date, since: dt.date | None = None) -> list[dt.date]:
    """The Fridays from the week after the newest one entered for EVERY commodity (or from `since`) to `upto` that are missing for
    at least one commodity — the weeks still to type in."""
    if since is None:
        newest = [max(s) for s in (data.get(c) or {} for c in COMMODITIES) if s]
        if not newest:
            return []
        since = min(newest) + dt.timedelta(days=7)
    out, d = [], last_friday(since) + (dt.timedelta(days=7) if since.weekday() != 4 else dt.timedelta(0))
    while d <= upto:
        if any(d not in (data.get(c) or {}) for c in COMMODITIES):
            out.append(d)
        d += dt.timedelta(days=7)
    return out


# ------------------------------------------------------- the workbook history ---
def _first_friday(year: int) -> dt.date:
    d = dt.date(year, 1, 1)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def parse_workbook(path: str, sheet: str = "Deliverable Stox") -> list[tuple]:
    """The old FOB Sheet workbook's 'Deliverable Stox' tab -> [(as_of, commodity, stocks_thousand_bu, 'workbook')].

    Layout (JSA FOB Sheet - August 2026.xlsx): a 'Soybeans' and a 'Corn' block, each a header row of years (2026, 2025 ... 2020,
    then an average column, skipped) over 52 week rows whose column B holds the current year's Fridays (formulas: the row below + 7).
    The past years are lined up by ROW, not by date, so row k (k = 0 for the first Friday of the year) is dated year Y's k-th Friday —
    exact for the current year, at most a week off for a past one (a seasonal chart does not mind). Blank cells (the current year
    after mid-June 2026) are skipped."""
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True)[sheet]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    out = []
    i = 0
    while i < len(grid):
        label = str(grid[i][0] or "").strip()
        commodity = {"soybeans": "Soybeans", "corn": "Corn"}.get(label.lower())
        if commodity is None:
            i += 1
            continue
        h = next(j for j in range(i + 1, len(grid)) if any(isinstance(v, int) and 2000 < v < 2100 for v in grid[j]))
        years = {c: v for c, v in enumerate(grid[h]) if isinstance(v, int) and 2000 < v < 2100}
        j = h + 1
        while j < len(grid) and isinstance(grid[j][1], (dt.date, dt.datetime)):
            row_date = grid[j][1].date() if isinstance(grid[j][1], dt.datetime) else grid[j][1]
            k = (row_date - _first_friday(row_date.year)).days // 7
            for c, year in years.items():
                v = grid[j][c] if c < len(grid[j]) else None
                if isinstance(v, (int, float)):
                    as_of = _first_friday(year) + dt.timedelta(days=7 * k)
                    if as_of.year == year:
                        out.append((as_of, commodity, float(v), "workbook"))
            j += 1
        i = j
    return out


def season_points(series: dict, years: list[int]) -> list[dict]:
    """[{'year', 'x' (the date on the REF_YEAR axis), 'date', 'stocks'}] for the given years, sorted."""
    pts = []
    for d, v in sorted(series.items()):
        if d.year in years:
            try:
                x = d.replace(year=REF_YEAR)
            except ValueError:
                continue
            pts.append({"year": d.year, "x": x, "date": d, "stocks": v})
    return pts


def average_points(series: dict, years: list[int]) -> list[dict]:
    """The average of `years` week by week (a week needs at least 3 of the years), placed on the REF_YEAR axis at the mean of those
    years' dates: [{'x', 'stocks', 'n'}]. Years line up by the k-th Friday of the year, as the workbook's rows did — not by ISO
    week, which puts a Jan 1 Friday (2021) in the previous year's week 53 and shifts that whole year by a week."""
    by_wk: dict = {}
    for d, v in series.items():
        if d.year in years:
            wk = max(0, (d - _first_friday(d.year)).days // 7)
            by_wk.setdefault(wk, []).append((d, v))
    out = []
    for wk, items in sorted(by_wk.items()):
        if len(items) < 3:
            continue
        days = [(d.replace(year=REF_YEAR) - dt.date(REF_YEAR, 1, 1)).days for d, _ in items]
        x = dt.date(REF_YEAR, 1, 1) + dt.timedelta(days=round(sum(days) / len(days)))
        out.append({"x": x, "stocks": sum(v for _, v in items) / len(items), "n": len(items)})
    return sorted(out, key=lambda p: p["x"])


def chart_years(series: dict, current: int, back: int = 6) -> list[int]:
    """The years the chart draws: the `back` years before `current` that have data, then `current`."""
    have = {d.year for d in series}
    return [y for y in range(current - back, current + 1) if y in have]


def average_years(series: dict, current: int, n: int = 5) -> list[int]:
    """The `n` completed years before `current` that have data (the dotted 'Average [first-last]' line)."""
    have = {d.year for d in series}
    return [y for y in range(current - n, current) if y in have]
