"""Load the deliverable-stocks history (CBOT corn + soybeans, weekly, thousand bushels) from the old FOB Sheet workbook's
'Deliverable Stox' tab into the `deliverable_stocks` table (see deliverable_stocks.py). One-off; safe to re-run (it upserts).

    python import_deliverable_stocks.py                     # preview: what would be written
    python import_deliverable_stocks.py --commit            # write to the archive (Snowflake, from this repo's .env)
    python import_deliverable_stocks.py --path <xlsx> ...   # another copy of the workbook

The workbook is copied to a temp file first (it may be open in Excel). Rows typed in the app ('manual') for the same week are
replaced only if --overwrite-manual is given; by default the workbook fills gaps and refreshes earlier workbook rows.
"""
import argparse
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = (r"C:\Users\KoltenPostin\John Stewart and Associates\JSA - Documents\St. Louis\JSA FOB Sheet\2026"
           r"\JSA FOB Sheet - August 2026.xlsx")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=DEFAULT)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--overwrite-manual", action="store_true")
    a = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"), override=True)       # by absolute path: never a stray SQLite write
    sys.path.insert(0, HERE)
    import db
    import deliverable_stocks as ds

    tmp = os.path.join(tempfile.mkdtemp(), "stox.xlsx")
    shutil.copyfile(a.path, tmp)
    rows = ds.parse_workbook(tmp)
    by = {}
    for d, c, v, _s in rows:
        by.setdefault(c, []).append((d, v))
    for c, items in sorted(by.items()):
        items.sort()
        years = sorted({d.year for d, _v in items})
        print(f"{c}: {len(items)} weeks, {items[0][0]} .. {items[-1][0]} ({years[0]}-{years[-1]}); "
              f"first {items[0][1]:,.0f}, last {items[-1][1]:,.0f} thousand bu")
    if not a.commit:
        print("preview only — add --commit to write")
        sys.exit(0)
    if db._backend() != "snowflake":
        sys.exit(f"refusing to write: the backend is {db._backend()}, not Snowflake (check this repo's .env)")
    have = ds.load_all()
    manual = {(d, c) for c, s in have.items() for d in s} if not a.overwrite_manual else set()
    src = {}
    if manual:
        conn, _ = db._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT as_of, commodity, source FROM {ds.TABLE}")
            src = {(str(x)[:10], c): s for x, c, s in cur.fetchall()}
        finally:
            conn.close()
    keep = [r for r in rows if src.get((r[0].isoformat(), r[1])) not in ("manual", "datamine")]
    n = ds.save(keep)
    print(f"wrote {n} rows ({len(rows) - len(keep)} weeks kept as typed in the app)")
    back = ds.load_all()
    print("now in the table:", {c: (len(s), min(s).isoformat(), max(s).isoformat()) for c, s in back.items()})
