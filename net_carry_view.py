"""net_carry_view.py - the HTML for the River FOB portal's Net Carry tab (pure strings, no Streamlit).

The numbers come from the vendored net_carry module; this only lays them out in the portal's own look: a
`.sheet-wrap` card (rounded, shadowed, 50-year watermark) holding a commodity banner, a dark header row and
zebra rows, like the FOB sheet itself. Net Carry's colour language is kept from the basis tracker:
green = inverse (+), red = carry (-), amber (#f28e2b accent on #fff4e5) = the top of net carry.

Everything is one line of HTML with no blank lines - Streamlit's markdown turns a blank line followed by an
indented line into a code block.
"""
from __future__ import annotations

import html

import net_carry as nc

AMBER, AMBER_BG = "#f28e2b", "#fff4e5"
GREEN, RED, GREY = "#0a7f3f", "#c0392b", "#64748b"

CSS = f"""
<style>
  .nc-scroll {{ overflow-x: auto; }}
  .nc-banner {{ color: #fff; font-weight: 700; padding: 10px 16px; text-align: center; font-size: 1.02rem;
      letter-spacing: .05em; text-shadow: 0 1px 2px rgba(0,0,0,.18); }}
  table.nc {{ width: 100%; min-width: 700px; border-collapse: collapse; font-size: 0.85rem; }}
  table.nc th {{ background: #32373c; color: #fff; font-weight: 600; font-size: 0.74rem; padding: 7px 9px;
      text-align: right; vertical-align: bottom; line-height: 1.25; border-right: 1px solid rgba(255,255,255,.15); }}
  table.nc th.l {{ text-align: left; }}
  table.nc td {{ padding: 7px 9px; text-align: right; border-bottom: 1px solid #f1f1f1; color: #333;
      font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  table.nc td.l {{ text-align: left; }}
  table.nc tr.band td {{ background: #fafbfc; }}
  table.nc tbody tr:hover td {{ background: #eef6fd; }}
  table.nc tr.top td, table.nc tbody tr.top:hover td {{ background: {AMBER_BG}; }}
  table.nc tr.top td:first-child {{ border-left: 4px solid {AMBER}; }}   /* a border, not an inset shadow: html2canvas (the PNG button) paints an inset shadow as a solid block */
  table.nc tr:last-child td {{ border-bottom: none; }}
  table.nc .nc-fut {{ color: {GREY}; }}
  table.nc .nc-dim {{ color: #94a3b8; }}
  table.nc .nc-flag {{ color: {RED}; font-weight: 700; }}
  table.nc .nc-tag {{ background: {AMBER}; color: #fff; font-size: 9px; font-weight: 700; letter-spacing: .05em;
      padding: 2px 7px; border-radius: 9px; margin-left: 8px; white-space: nowrap; vertical-align: 1px; }}
  .nc-callout {{ background: #fff7ed; border: 1px solid #fed7aa; border-left: 5px solid {AMBER}; border-radius: 8px;
      padding: 10px 14px; margin: 8px 0; font-size: 0.92rem; line-height: 1.5; color: #7c2d12; }}
  .nc-callout .nc-mark {{ color: {AMBER}; font-size: 15px; margin-right: 6px; }}
  .nc-summary {{ font-size: 0.82rem; color: {GREY}; margin: 6px 0 4px; }}
  .nc-kicker {{ margin: 22px 0 2px; font-size: 0.7rem; color: {GREY}; font-weight: 700; letter-spacing: .1em;
      text-transform: uppercase; }}
  .nc-compare {{ padding: 10px 12px 6px; }}
  .nc-compare table {{ width: 100%; }}
  /* a phone scrolls these tables sideways: keep the month column in view, and drop the pill that would widen it */
  table.nc td:first-child, table.nc th:first-child,
  .nc-compare table td:first-child, .nc-compare table th:first-child {{ position: sticky; left: 0; z-index: 2; }}
  table.nc td:first-child, .nc-compare table td:first-child, .nc-compare table th:first-child {{ background: #fff; }}
  table.nc th:first-child {{ background: #32373c; }}
  table.nc tr.band td:first-child {{ background: #fafbfc; }}
  table.nc tr.top td:first-child {{ background: {AMBER_BG}; }}
  @media (max-width: 640px) {{ table.nc .nc-tag {{ display: none; }} }}
</style>
"""


def fmt(v, dec: int = 1, sign: bool = True) -> str:
    """A cents value as text ('+12.3' / '12.3'); an em dash for None."""
    if v is None:
        return "—"
    return f"{v:+.{dec}f}" if sign else f"{v:.{dec}f}"


def fmt_quoted(v) -> str:
    """Quoted basis: whole cents when it is whole, else one decimal."""
    return fmt(v, 0 if float(v).is_integer() else 1)


def summary_html(ref_sym, front_mode: bool, ref_price, per_month, rate_pct, anchor_lbl) -> str:
    """'Reference ZCZ26 (front delivery's futures) · board 4.97 · interest 2.5¢/mo @ 6.13% · carry from Oct'."""
    bits = [f"Reference <b>{html.escape(ref_sym or '—')}</b> "
            + ("(front delivery's futures)" if front_mode else "(nearest new-crop)")]
    if ref_price is not None:
        bits.append(f"board {ref_price / 100:.2f}")
    if per_month is not None:
        bits.append(f"interest <b>{per_month:.2f}¢/mo</b> @ {rate_pct:.2f}%")
    bits.append(f"carry from <b>{html.escape(anchor_lbl)}</b>")
    return f'<div class="nc-summary">{" · ".join(bits)}</div>'


def callout_html(top) -> str:
    """The amber 'top of net carry' box; '' when there is no top."""
    if not top:
        return ""
    return ('<div class="nc-callout"><span class="nc-mark">▲</span>'
            f'<b>{html.escape(nc.top_headline(top))}.</b> {html.escape(nc.top_detail(top))}</div>')


def kicker_html(text: str) -> str:
    return f'<div class="nc-kicker">{html.escape(text)}</div>'


def carry_table_html(rows, top_row, ref_sym, title: str, banner=("#0693e3", "#0573b8"), table_id: str = "snap_nc_table") -> str:
    """The 8-column Net Carry table in a `.sheet-wrap` card.

    rows: net_carry.compute_net_carry rows. top_row: the table row index holding the top of net carry (or None) -
    tagged, tinted amber, its Net of Interest in bold. Same cells as the basis tracker's table, so the two
    agree to the decimal."""
    ref = ref_sym or ""
    # the zero-width space after the slash lets the last header wrap on a narrow card
    hdr = ("Delivery", "Futures", "Quoted basis", f"Futures spread vs {ref or 'ref'}", f"Basis {ref}".strip(),
           "Interest", f"Net of Interest {ref}".strip(), "Inverse(+)/​Carry(−)")
    th = "".join(f'<th class="{"l" if i < 2 else ""}">{html.escape(h)}</th>' for i, h in enumerate(hdr))
    body = ""
    for ri, r in enumerate(rows):
        interest = "" if r.months == 0 else fmt(r.interest, 1, sign=False)
        if r.carry is None:
            carry = ""
        else:
            col = GREEN if r.carry > 0.0049 else RED if r.carry < -0.0049 else GREY
            carry = f'<span style="color:{col};font-weight:600">{fmt(r.carry)}</span>'
        flag = "" if r.converted else (' <span class="nc-flag" title="no futures spread to the reference '
                                        '- raw basis">·</span>')
        if r.credit is None:
            spread = '<span class="nc-dim">—</span>'
        elif abs(r.credit) < 0.05:                       # quoted off the reference itself: nothing to credit
            spread = '<span class="nc-dim">0.0</span>'
        else:
            spread = fmt(r.credit)
        delivery = f"{html.escape(r.delivery)}{flag}"
        net = fmt(r.net)
        is_top = ri == top_row
        if is_top:
            delivery += '<span class="nc-tag">▲ TOP OF NET CARRY</span>'
            net = f"<b>{net}</b>"
        cells = [delivery, f'<span class="nc-fut">{html.escape(r.futures or "—")}</span>', fmt_quoted(r.raw_basis),
                 spread, fmt(r.basis_ref), interest, net, carry]
        tds = "".join(f'<td class="{"l" if i < 2 else ""}">{c}</td>' for i, c in enumerate(cells))
        cls = "top" if is_top else ("band" if ri % 2 == 0 else "")
        body += f'<tr class="{cls}">{tds}</tr>'
    c0, c1 = banner
    # the banner sits OUTSIDE the scrolling area, so a phone always reads the whole title
    return (f'<div class="sheet-wrap" id="{table_id}">'
            f'<div class="nc-banner" style="background:linear-gradient(135deg,{c0},{c1})">{html.escape(title)}</div>'
            f'<div class="nc-scroll"><table class="nc"><thead><tr>{th}</tr></thead>'
            f'<tbody>{body}</tbody></table></div></div>')


def compare_card_html(inner_html: str, table_id: str = "snap_nc_compare") -> str:
    """Wrap net_carry_compare.render_html's output in the portal's card."""
    return f'<div class="sheet-wrap nc-compare" id="{table_id}">{inner_html}</div>'


def anchor_note(meta: dict, anchor_month: int, anchor_lbl: str) -> str:
    """'' when interest starts in the chosen month; else why it starts elsewhere (the sheet has no delivery in
    that month, so the module falls back to the first month on the ladder)."""
    ym = (meta or {}).get("anchor_ym")
    if not ym or ym[1] == anchor_month:
        return ""
    return (f"There is no {anchor_lbl} delivery on this sheet, so interest starts at its first month, "
            f"{nc._ABBR[ym[1]]} {ym[0]}.")
