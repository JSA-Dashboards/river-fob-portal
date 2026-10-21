"""
Parse the daily Barge Freight and CIF NOLA tables (copy-pasted from the source)
into the portal's input structures.

Both tables are month-down-the-side, with a couple of spot rows (TW/NW) that we
skip. CIF values are in cents (82.0 -> 0.82) with a contract-month letter beside
each; freight values are percentages (550% -> 5.50). Tab-separated (Excel copy),
with a 2+-space fallback.
"""
import re
import datetime as dt

# Source month-row label -> sheet month key. TW/NW (spot week) are skipped.
# Full set so the rolling window works year-round (June/July spelled out to
# match the sheet's column labels; the rest 3-letter).
_MONTHS = {"JAN": "Jan", "JANUARY": "Jan", "FEB": "Feb", "FEBRUARY": "Feb",
           "MAR": "Mar", "MARCH": "Mar", "APR": "Apr", "APRIL": "Apr",
           "MAY": "May", "JUNE": "June", "JUN": "June", "JULY": "July",
           "JUL": "July", "AUG": "Aug", "AUGUST": "Aug", "SEP": "Sep",
           "SEPT": "Sep", "SEPTEMBER": "Sep", "OCT": "Oct", "OCTOBER": "Oct",
           "NOV": "Nov", "NOVEMBER": "Nov", "DEC": "Dec", "DECEMBER": "Dec"}

_COMMODITIES = {"CORN": "Corn", "BEANS": "Soybeans", "SOYBEANS": "Soybeans",
                "WHEAT": "Wheat"}  # MILO intentionally absent

# Freight source column -> sheet freight region(s). L OHIO duplicates OHIO.
_FREIGHT_MAP = {
    "ILL": ["IL"], "OHIO": ["Ohio"],
    "MM": ["Davenport South", "McGregor South"],
    "CITIES": ["Upper Miss"], "STL": ["STL"], "MTCT": ["Lower Miss"],
}
_FREIGHT_HDRS = set(_FREIGHT_MAP)


def _split(line):
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    return [c.strip() for c in re.split(r" {2,}", line)]


def _rows(text):
    return [_split(ln) for ln in text.splitlines() if ln.strip()]


def parse_cif(text):
    """-> ({commodity: {month: basis}}, {commodity: {month: contract_letter}}) or (None, error)."""
    rows = _rows(text)
    col = {}
    for r in rows:
        for i, c in enumerate(r):
            key = _COMMODITIES.get(c.upper())
            if key and key not in col:
                col[key] = i
        if col:
            break
    if not col:
        return None, "Couldn't find a CORN / BEANS / WHEAT header row."

    cif = {c: {} for c in col}
    contracts = {c: {} for c in col}
    for r in rows:
        mon = _MONTHS.get(r[0].upper()) if r else None
        if not mon:
            continue
        for commodity, ci in col.items():
            if ci >= len(r) or r[ci] == "":
                continue
            try:
                cif[commodity][mon] = float(r[ci].replace("%", "")) / 100.0
            except ValueError:
                continue
            if ci + 1 < len(r) and r[ci + 1]:
                contracts[commodity][mon] = r[ci + 1].upper()
    return {"cif": cif, "contracts": contracts}, None


# ── the Ceres barge freight table (Kolten 2026-10-21) ────────────────────────────────────────────────
# Two header rows (river over location: "St. Paul / Savage", "St. Louis / 14'", "Ohio / Jeff/Cincy" ...), period rows down the side
# ("WK 10/18", "NOV", "AMJJ"), each cell a bid/ask pair ("775/825"): the BID is used. Columns -> the sheet's freight regions:
# St. Louis 14' is the sheet's STL (Doug's Bid Sheet matched it in every month on 10/19; the 12' column never did), Ohio Jeff/Cincy
# its Ohio (Ohio Lower is not used, as L OHIO is not in the old format); MTCT = Lower Miss, St. Paul / Savage = Upper Miss.
_MONTH_KEYS = ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ceres_region(label):
    """A Ceres column's combined header ('St. Louis 14'') -> the sheet's freight region, or None for a column the sheet does not use."""
    t = " ".join(label.upper().split())
    if "MTCT" in t:
        return "Lower Miss"
    if "ST. PAUL" in t or "ST PAUL" in t or "SAVAGE" in t:
        return "Upper Miss"
    if "MCGREGOR" in t:
        return "McGregor South"
    if "DAVENPORT" in t:
        return "Davenport South"
    if ("ST. LOUIS" in t or "ST LOUIS" in t) and "14" in t:
        return "STL"
    if "ILLINOIS" in t:
        return "IL"
    if "OHIO" in t and ("JEFF" in t or "CINCY" in t or "CINCI" in t):
        return "Ohio"
    return None


def _period_months(label):
    """A Ceres period label -> (months, rank): a month ('NOV') ranks 3, a week ('WK 10/18' -> Oct) 2, a package of month initials
    ('AMJJ' -> Apr, May, June, July; 'JFM') 1. ([], 0) for anything else."""
    u = label.upper().strip().rstrip(".")
    if u in _MONTHS:
        return [_MONTHS[u]], 3
    m = re.match(r"^(?:WK|WEEK)\.?\s*(\d{1,2})/(\d{1,2})", u)
    if m and 1 <= int(m.group(1)) <= 12:
        return [_MONTH_KEYS[int(m.group(1)) - 1]], 2
    if re.fullmatch(r"[A-Z]{2,6}", u):
        initials = [k[0].upper() for k in _MONTH_KEYS]
        for s in range(12):
            if all(initials[(s + i) % 12] == ch for i, ch in enumerate(u)):
                return [_MONTH_KEYS[(s + i) % 12] for i in range(len(u))], 1
    return [], 0


def _bid(cell):
    """'775/825' -> 7.75 (the bid, % of tariff / 100); '775' -> 7.75; '' -> None (no quote: the river is closed or not quoted)."""
    s = cell.replace("%", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s.split("/")[0].strip()) / 100.0
    except ValueError:
        return None


_CERES_NO_TABS = ("This looks like the Ceres freight table, but it arrived without its column breaks (tabs), so the blank cells "
                  "(closed months) can't be placed and the numbers would shift columns. Nothing was filled — copy the table again "
                  "straight from the email or Excel and paste it here.")


def parse_ceres_freight(text):
    """The Ceres bid/ask barge freight table -> ({'freight': {region: {month: tariff_mult or None}}, 'date', 'format': 'ceres',
    'used': {region: column label}}, None), or (None, error) when it is not that table.

    Only the BID of each cell is used. A month row wins over a week row, and a week row over a package (AMJJ fills only the months no
    row of their own covers); of several weeks in one month the first (the current week) is used. A BLANK cell in a column the sheet
    uses comes back as None for that month: the reach is closed / not quoted, and the pasted table replaces yesterday's number."""
    if "\t" not in text and re.search(r"st\.?\s*paul|mcgregor|davenport|st\.?\s*louis", text, re.I):
        # Without the tabs a blank cell leaves no trace, so every value after it lands one column early: a copy that lost its tabs once
        # read St. Louis's January as Upper Miss's. Refuse instead of guessing.
        return None, (_CERES_NO_TABS)
    raw = [ln for ln in text.splitlines() if ln.strip()]
    rows = [[c.strip() for c in ln.split("\t")] for ln in raw]
    first = next((i for i, r in enumerate(rows) if r and _period_months(r[0])[1]), None)
    if first is None or first == 0:
        return None, "no period rows (WK / month / AMJJ) under a header"
    width = max(len(r) for r in rows)
    labels = [" ".join(r[i] for r in rows[:first] if i < len(r) and r[i]) for i in range(width)]
    cols = {i: _ceres_region(lab) for i, lab in enumerate(labels) if i > 0}
    cols = {i: reg for i, reg in cols.items() if reg}
    if len(cols) < 3:
        return None, "not the Ceres table (no St. Paul / McGregor / Davenport / St. Louis / Illinois / Ohio / MTCT columns)"
    best: dict = {}                                                        # (region, month) -> (rank, value)
    for r in rows[first:]:
        months, rank = _period_months(r[0]) if r else ([], 0)
        for mon in months:
            for ci, region in cols.items():
                val = _bid(r[ci]) if ci < len(r) else None
                have = best.get((region, mon))
                if have is None or rank > have[0]:                         # a higher-ranked row wins; the first of equals stays
                    best[(region, mon)] = (rank, val)
    freight: dict = {}
    for (region, mon), (_rank, val) in best.items():
        freight.setdefault(region, {})[mon] = val
    return {"freight": freight, "date": _find_date(text), "format": "ceres",
            "used": {cols[i]: labels[i] for i in sorted(cols)}}, None


def parse_freight(text):
    """-> ({region: {month: tariff_mult}}, detected_date) or (None, error). Reads the Ceres bid/ask table (parse_ceres_freight) or the
    older single-header one (ILL / OHIO / MM / CITIES / STL / MTCT)."""
    ceres, why = parse_ceres_freight(text)
    if ceres is not None:
        return ceres, None
    if why == _CERES_NO_TABS:
        return None, why
    rows = _rows(text)
    col = {}
    for r in rows:
        for i, c in enumerate(r):
            key = c.upper().rstrip(".")
            if key in _FREIGHT_HDRS and key not in col:
                col[key] = i
        if "MTCT" in col:
            break
    if not col:
        return None, ("Couldn't find a freight header (ILL/OHIO/MM/CITIES/STL/MTCT), or the Ceres table's "
                      "(St. Paul / McGregor / Davenport / St. Louis / Illinois / Ohio / MTCT over WK / month rows).")

    freight = {}
    for r in rows:
        mon = _MONTHS.get(r[0].upper()) if r else None
        if not mon:
            continue
        for src, ci in col.items():
            if ci >= len(r) or r[ci] == "":
                continue
            try:
                val = float(r[ci].replace("%", "")) / 100.0
            except ValueError:
                continue
            for region in _FREIGHT_MAP[src]:
                freight.setdefault(region, {})[mon] = val
    return {"freight": freight, "date": _find_date(text)}, None


_COMM_LETTER = {"C": "Corn", "S": "Soybeans", "W": "Wheat"}
_SYM_RE = re.compile(r"Z([CSW])([FGHJKMNQUVXZ])(\d{2})")
_FRAC_RE = re.compile(r"^(\d+)[\-'’](\d)$")   # grain fractional: 427'6 = 427 6/8


def _fut_price(tok):
    """A futures price token -> float, or None. Accepts decimals and grain
    fractional notation (427'6 or 427-6 = 427 and 6/8)."""
    tok = str(tok).strip().replace(",", "").replace("$", "")
    if not tok:
        return None
    m = _FRAC_RE.match(tok)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 8.0
    try:
        return float(tok)
    except ValueError:
        return None


def parse_futures(text):
    """-> ({commodity: {contract_letter: $/bu price}}) or (None, error).

    Handles symbols like ZCN26 (corn July '26) whether the price is in the next
    column, glued to the symbol, or on the same line separated by a single
    space; decimals or grain fractionals (427'6). Cent quotes (>100) convert to
    $/bu; dollar quotes are kept as-is.
    """
    out = {}
    for r in _rows(text):
        sym, tail = None, []
        for i, cell in enumerate(r):
            m = _SYM_RE.match(str(cell).strip().upper())
            if m:
                sym = m
                after = str(cell).strip()[m.end():]     # price glued to symbol?
                tail = ([after] if after else []) + list(r[i + 1:])
                break
        if not sym:
            continue
        price = None
        for chunk in tail:
            for piece in re.split(r"\s+", str(chunk).strip()):
                price = _fut_price(piece)
                if price is not None:
                    break
            if price is not None:
                break
        if price is None:
            continue
        if price > 100:                                  # cents -> $/bu
            price /= 100.0
        out.setdefault(_COMM_LETTER[sym.group(1)], {})[sym.group(2)] = round(price, 4)
    if not out:
        return None, "Couldn't find futures symbols like ZCN26."
    return {"futures": out}, None


def _find_date(text):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not m:
        return None
    mo, day, yr = (int(x) for x in m.groups())
    yr = 2000 + yr if yr < 100 else yr
    try:
        return dt.date(yr, mo, day)
    except ValueError:
        return None
