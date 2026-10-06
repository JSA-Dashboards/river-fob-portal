"""net_carry_data.py - the adapter between the River FOB sheet and the vendored Net Carry modules.

The Net Carry logic (net_carry.py, net_carry_chart.py, net_carry_compare.py, carry_rate.py) is copied
unchanged from the basis tracker (see sync_carry_modules.py there). It reads a location's forward curve as
    items = [{'delivery': 'Oct 2026', 'futures': 'ZCZ26', 'basis': -6.2}]      (basis in CENTS/bu)
    curve = {'ZCZ26': 497.5, ...}                                              (futures in CENTS/bu)
and this module builds both from what the river sheet stores. Pure functions only (no Streamlit, no DB),
so every step can be unit-tested; the app hands in the sheet's columns, CBOT row and FOB row. The one exception
is `futures_history` at the bottom (the Return to Carry section's futures settlements from the basis tracker's
FUTURES_PRICES): its query goes through an injectable `rows_fn`, so the tests run it against a throwaway SQLite.

How the sheet maps onto the module's inputs
  * DELIVERY. A sheet column is a calendar MONTH label ('Oct', 'July', 'Sept', older sheets also 'Spot') and has
    no year. The window rolls forward from the as-of month (fob_model.months_for), so the first month gets the
    year that puts it at/near the sheet's date and every later column is the next such month ('Jan' after
    'Dec' is next year). A 'Spot' column is the prompt bid in the as-of month. Each delivery is handed over as
    'Oct 2026' / 'Spot Oct 2026' - an explicit year, because delivery_period would otherwise guess it from
    the futures contract (wrong for a Jan delivery quoted off last December's contract).
  * FUTURES. The sheet carries one contract code per column ('CZ', 'SX', 'WH'; prefix = commodity, last
    letter = CME month code) and one CBOT price per column (the same contract repeats its price). The code has
    no year either, so the contract is the occurrence of that month letter NEAREST the delivery month (from
    5 months before to 6 after): Oct 2026 + 'CZ' -> ZCZ26; Dec 2026 + 'SF' -> ZSF27 (soybean Jan); Dec 2023 +
    'CH' -> ZCH24; and a sheet that quotes Oct 2025 off the Sept contract still reads ZCU25, not ZCU26.
    Roots: Corn ZC, Soybeans ZS, Wheat ZW (Chicago SRW - the river sheet's wheat, see massive_futures.PRODUCT).
  * UNITS. FOB and the CBOT row are $/bu on the sheet; both go to the module in cents (x100).
  * NO GUESSING. A sheet with no usable futures, or whose month labels don't line up with its date (stale
    headers on a handful of 2025 sheets), comes back with a plain-language `problem` and no items.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

ROOT = {"Corn": "ZC", "Soybeans": "ZS", "Wheat": "ZW"}

_ABBR = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
         7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
# Every spelling a sheet column label has been saved with (abbreviations, full names, 'Sept'). Exact matches
# only: a three-letter prefix would read 'junk' as June.
_MONTH_NUM = {}
for _n, (_a, _full) in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                                  ("may", "may"), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                                  ("sep", "september"), ("oct", "october"), ("nov", "november"),
                                  ("dec", "december")), start=1):
    _MONTH_NUM[_a] = _MONTH_NUM[_full] = _n
_MONTH_NUM["sept"] = 9
_CODE_MONTH = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
               "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}

# A CBOT grain price outside this band ($/bu) is junk (a disconnected add-in cached 25/26 for every month).
# Same band the daily import uses to refuse a CBOT row.
PRICE_BAND = (1.5, 20.0)
# How far after the sheet's date its first month may start (0 = the as-of month, the normal case; the
# few sheets that begin one or two months out have a 'Spot' column for the months in between).
MAX_LEAD = 2
# The archive holds futures only for sheets saved from this date on (first row of futures_history).
ARCHIVE_FUTURES_START = date(2023, 2, 1)


# ── labels, months, contracts ────────────────────────────────────────────────────────────────
def is_spot(label) -> bool:
    return str(label or "").strip().lower() == "spot"


def month_of(label) -> int | None:
    """Calendar month of a sheet column label: 'Oct' 10, 'June' 6, 'Sept' 9, 'December' 12; None for
    'Spot', blanks and anything that isn't a month (older imports sometimes stored numbers)."""
    return _MONTH_NUM.get(str(label or "").strip().lower().rstrip("."))


def month_index(ym) -> int:
    """(year, month) -> a running month count, so months can be subtracted."""
    return ym[0] * 12 + ym[1] - 1


def contract_ym(code, delivery_ym) -> tuple | None:
    """(year, month) of the futures contract a column is quoted off: the occurrence of the code's month
    letter nearest the delivery month, from 5 months before it to 6 after. None for an unknown letter."""
    letter = str(code or "").strip().upper()[-1:]
    cm = _CODE_MONTH.get(letter)
    if cm is None or not delivery_ym:
        return None
    off = ((cm - delivery_ym[1] + 5) % 12) - 5
    i = month_index(delivery_ym) + off
    return (i // 12, i % 12 + 1)


def contract_symbol(commodity, code, delivery_ym) -> str | None:
    """'ZCZ26' for Corn + 'CZ' + Oct 2026; None when the code or the commodity can't be resolved."""
    root = ROOT.get(commodity)
    cym = contract_ym(code, delivery_ym)
    if not root or cym is None:
        return None
    return f"{root}{str(code).strip().upper()[-1]}{cym[0] % 100:02d}"


def delivery_label(ym, spot=False) -> str:
    """'Oct 2026' / 'Spot Oct 2026' - the label net_carry reads, with the year spelled out."""
    return f"{'Spot ' if spot else ''}{_ABBR[ym[1]]} {ym[0]}"


def _num(v) -> float | None:
    """float, or None for None / NaN / non-numbers (a blank cell in the live editor is NaN)."""
    try:
        if v is None:
            return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# ── the sheet ────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Column:
    label: str              # the sheet's own column label ('Oct', 'July', 'Spot') - the key into its rows
    contract: str           # the sheet's contract code ('CZ')
    ym: tuple               # (year, month) of the delivery
    spot: bool              # a 'Spot' (prompt) column
    symbol: str | None      # the CME contract, 'ZCZ26' (None when the code can't be resolved)
    delivery: str           # 'Oct 2026' / 'Spot Oct 2026'


@dataclass
class Sheet:
    commodity: str
    as_of: date
    columns: list = field(default_factory=list)    # list[Column], in sheet order
    curve: dict = field(default_factory=dict)      # {'ZCZ26': 497.5} cents/bu - the contracts the sheet prices
    notes: list = field(default_factory=list)      # things quietly set aside (shown as captions)
    problem: str | None = None                     # why nothing can be netted (None = good to go)


def place_columns(commodity, as_of: date, columns) -> tuple[list, list, str | None]:
    """Give each sheet column its real delivery month and futures contract.

    columns: [(label, contract_code), ...] in sheet order. Returns (placed, notes, problem); `placed` is []
    when `problem` is set. The first month must start at the sheet's date (0-2 months out); a sheet whose
    labels start before its date, or further out, has stale headers and is refused rather than guessed at."""
    cols = [(l, c) for l, c in (columns or [])]
    if not cols:
        return [], [], "This sheet has no month / contract record saved, so its deliveries can't be matched to futures."
    first_m = next((month_of(l) for l, _ in cols if not is_spot(l) and month_of(l)), None)
    diff = 0
    if first_m is not None:
        diff = ((first_m - as_of.month + 6) % 12) - 6              # months from the as-of month, -6..+5
        if not 0 <= diff <= MAX_LEAD:
            first_lbl = next(l for l, _ in cols if not is_spot(l) and month_of(l))
            where = (f"{abs(diff)} month{'s' if abs(diff) != 1 else ''} "
                     f"{'before' if diff < 0 else 'after'}")
            return [], [], (f"The month labels saved on this sheet don't line up with its date: its first month is "
                            f"'{first_lbl}', {where} {as_of:%b %d, %Y}. Those headers are stale, so the deliveries "
                            "can't be placed reliably.")
    elif not any(is_spot(l) for l, _ in cols):
        return [], [], "None of this sheet's columns is a month, so its deliveries can't be placed."

    notes, placed = [], []
    base = month_index((as_of.year, as_of.month)) + diff
    prev = None                                                    # running index of the last month column
    seen_spot = False
    for label, code in cols:
        if is_spot(label):
            if seen_spot:
                notes.append("Dropped a repeated 'Spot' column.")
                continue
            seen_spot = True
            ym, spot = (as_of.year, as_of.month), True
        else:
            m = month_of(label)
            if m is None:
                notes.append(f"Ignored column '{label}' (not a month).")
                continue
            if prev is None:
                i = base
            else:
                pm = prev % 12 + 1
                if m == pm:
                    notes.append(f"Dropped a repeated '{label}' column.")
                    continue
                i = prev + ((m - pm - 1) % 12) + 1                  # the next such month after the previous one
            prev = i
            ym, spot = (i // 12, i % 12 + 1), False
        sym = contract_symbol(commodity, code, ym)
        if sym is None:
            notes.append(f"{label}: no usable contract code ('{code or ''}'), so its basis stays as quoted.")
        placed.append(Column(label=str(label), contract=str(code or ""), ym=ym, spot=spot,
                             symbol=sym, delivery=delivery_label(ym, spot)))
    return placed, notes, None


def curve_from_row(columns, fut_row, lo=PRICE_BAND[0], hi=PRICE_BAND[1]) -> tuple[dict, list]:
    """{symbol: cents} from the CBOT row (a $/bu price per column label). Each contract takes the first price
    seen for it, like fob_model.futures_by_contract; prices outside the plausible band or blank are skipped.
    Returns (curve, notes)."""
    curve, notes = {}, []
    for col in columns:
        if col.symbol is None:
            continue
        p = _num((fut_row or {}).get(col.label))
        if p is None:
            continue
        if not lo <= p <= hi:
            # no dollar signs here: two of them in one Streamlit caption would turn the text into LaTeX
            notes.append(f"Ignored the futures price {p:g} on {col.label} (outside the plausible "
                         f"{lo:g}-{hi:g} dollars per bushel).")
            continue
        cents = round(p * 100.0, 6)
        if col.symbol in curve:
            if abs(curve[col.symbol] - cents) > 1e-6:
                notes.append(f"{col.symbol} has two different prices on this sheet; using {curve[col.symbol] / 100:g}.")
            continue
        curve[col.symbol] = cents
    return curve, notes


def no_futures_message(as_of: date, live: bool = False) -> str:
    if live:
        return ("No futures prices are on the working sheet, so there is no board to re-express the basis against "
                "or to charge interest on. Paste the futures table or pull the live CBOT board (the 🔄 button) on "
                "the Inputs tab. Net carry needs them and will not guess.")
    msg = ("No futures prices were saved with this sheet, so there is no board to re-express the basis against "
           "or to charge interest on. Net carry needs them and will not guess.")
    if as_of < ARCHIVE_FUTURES_START:
        msg += (f" The archive began saving futures with the sheet in {ARCHIVE_FUTURES_START:%b %Y}; "
                "earlier days kept only CIF and freight.")
    return msg


def build_sheet(commodity, as_of: date, columns, fut_row, live: bool = False) -> Sheet:
    """The sheet's columns placed in time, plus its futures curve in cents.

    columns: [(month_label, contract_code)] in sheet order. fut_row: {month_label: $/bu} (the CBOT row).
    live: the working sheet rather than an archived day (only changes the wording of the no-futures message).
    `problem` is set (and the columns/curve left empty) when the sheet can't be netted."""
    if commodity not in ROOT:
        return Sheet(commodity, as_of, problem=f"No futures root is known for '{commodity}'.")
    placed, notes, problem = place_columns(commodity, as_of, columns)
    if problem:
        return Sheet(commodity, as_of, notes=notes, problem=problem)
    curve, cnotes = curve_from_row(placed, fut_row)
    notes = notes + cnotes
    if not curve:
        return Sheet(commodity, as_of, columns=placed, curve={}, notes=notes, problem=no_futures_message(as_of, live))
    return Sheet(commodity, as_of, columns=placed, curve=curve, notes=notes)


def items_for(sheet: Sheet, fob_row) -> list[dict]:
    """net_carry items for one location: a delivery per column that has an FOB value, basis in cents.

    fob_row: {month_label: FOB $/bu} (fob_model.compute_fob_grid(...)[location]). A month with no value (a
    closed reach, a blank CIF) is simply left out - the ladder skips it and charges interest by calendar days."""
    out = []
    for col in sheet.columns:
        v = _num((fob_row or {}).get(col.label))
        if v is None:
            continue
        out.append({"delivery": col.delivery, "futures": col.symbol, "basis": round(v * 100.0, 6)})
    return out


# ── which locations to compare ───────────────────────────────────────────────────────────────
def default_peers(main: str, places, n: int = 3) -> list[str]:
    """The `n` locations to compare `main` with by default: the nearest ones on the same river reach, nearest
    = the smallest gap in tariff factor (the factor is the $/ton to NOLA, i.e. how far down the river a
    location sits). A reach with fewer than `n` others (STL is alone in its own) is topped up with the closest
    factors from the neighbouring reaches.

    places: [(name, reach, factor)] in display order (ties keep that order)."""
    me = next((p for p in places if p[0] == main), None)
    if me is None:
        return []
    _, reach, factor = me
    others = [(abs(f - factor), i, name, r) for i, (name, r, f) in enumerate(places) if name != main]
    same = sorted(o for o in others if o[3] == reach)
    rest = sorted(o for o in others if o[3] != reach)
    return [o[2] for o in (same + rest)[:n]]


# ── the futures history behind the Return to Carry section ─────────────────────────────────────
# FUTURES_PRICES (basis tracker, Snowflake JSA.BASIS_TRACKER): one row per contract per day, ZC / ZS from 2006-11. The portal's own
# futures_history (RIVER_FOB) starts in 2023, so the return history reads the tracker's table through the same connection as the
# River Bids tab (bids_data).
_FUT_HISTORY_SQL = "SELECT date, symbol, price_cents FROM futures_prices WHERE symbol LIKE %s AND date >= %s"


def futures_history(root: str, rows_fn=None, since: str = "2004-01-01") -> dict:
    """{date: {symbol: cents}} for every contract whose symbol starts with `root` ('ZC' corn, 'ZS' soybeans): the stored settlements over
    the analyst's sheet weeks that come before them (return_to_carry_data.load_sheet_futures). One query; `rows_fn(sql, params) ->
    [dict]` defaults to bids_data's Snowflake reader (the tests hand it a throwaway SQLite)."""
    import return_to_carry_data as rd
    if rows_fn is None:
        import bids_data
        rows_fn = bids_data._sf_rows if bids_data._use_snowflake() else bids_data._pg_rows
    return rd.merge_futures(rd.load_sheet_futures(root=root), rd.futures_map(rows_fn(_FUT_HISTORY_SQL, (root + "%", since))))
