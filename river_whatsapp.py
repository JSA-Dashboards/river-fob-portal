"""The River FOB Daily on WhatsApp, as PNGs (Kolten 2026-11-09: "send via whats app as well ... Via PNGs").

river_email.py decides when and what; this draws its tables as PNGs and hands them to the WhatsApp bridge on the droplet (teams-broadcast's
whatsapp-service, localhost:3001). A table is drawn from the same rows as the email's HTML, as an SVG rasterised by vl_convert, the tool that draws the
charts: the droplet has no browser, and a browser wouldn't fit beside its other jobs in 2 GB.

Env: RIVER_WA_CHATS (comma-separated WhatsApp chat ids; unset = no WhatsApp), RIVER_WA_SESSION (whose linked account sends; default below), WA_API_KEY (the bridge's).
"""
import base64
import html as H
import os
import re
import textwrap

BRIDGE = "http://127.0.0.1:3001"
DEFAULT_SESSION = "Kolten Postin"
TARGET_WIDTH = 1500              # WhatsApp recompresses anything much past 1600 px
PAD = 16
FONT = "Liberation Sans, Arial, Helvetica, sans-serif"
LINE = "#e5e7eb"


class NotReady(Exception):
    """The bridge took nothing (down, or the account isn't connected), so it's safe to try again."""


class MaybeSent(Exception):
    """Something went wrong after the bridge may have posted: don't try again today."""


def _wrap(text, width):
    return textwrap.wrap(text, width) or [""]


def table_svg(rows, widths, title=None, title_color="#32373c", note=None):
    """An SVG of rows of cells (river_email._cell): one <rect> and <text> per cell, columns of the given widths."""
    total = sum(widths)
    W = total + 2 * PAD
    y = PAD
    out = []

    def text(x, base, s, size, color, anchor="start", bold=False, italic=False):
        out.append(f'<text x="{x}" y="{base}" font-family="{FONT}" font-size="{size}" fill="{color}" text-anchor="{anchor}"'
                   f'{" font-weight=" + chr(34) + "700" + chr(34) if bold else ""}{" font-style=" + chr(34) + "italic" + chr(34) if italic else ""}'
                   f'>{H.escape(s)}</text>')

    if title:
        text(PAD, y + 22, title, 26, title_color, bold=True)
        y += 42
    for row in rows:
        h = 46 if any(c["span"] > 1 for c in row) else 42
        x, col = PAD, 0
        for c in row:
            w = sum(widths[col:col + c["span"]])
            if c["bg"]:
                out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c["bg"]}"/>')
            if c["text"]:
                size = c["size"] + 7
                base = y + h / 2 + size * 0.35
                inner = 12 + c["indent"]
                tx, anchor = {"left": (x + inner, "start"), "right": (x + w - inner, "end"), "center": (x + w / 2, "middle")}[c["align"]]
                # The marker isn't in the font, and a missing glyph drops the whole line into a fallback typeface.
                text(tx, base, c["text"].removeprefix("▸ "), size, c["color"], anchor, c["bold"], c["italic"])
            x += w
            col += c["span"]
        if any(c["border"] for c in row):
            out.append(f'<line x1="{PAD}" x2="{PAD + total}" y1="{y + h}" y2="{y + h}" stroke="{LINE}"/>')
        y += h
    if note:
        y += 8
        for line in _wrap(note, 118):
            y += 22
            text(PAD, y, line, 16, "#6b7280")
    height = y + PAD
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}">'
            f'<rect width="{W}" height="{height}" fill="#ffffff"/>{"".join(out)}</svg>')


def table_png(rows, widths, **kw):
    import vl_convert as vlc
    svg = table_svg(rows, widths, **kw)
    return vlc.svg_to_png(svg, scale=TARGET_WIDTH / (sum(widths) + 2 * PAD))


def disclaimer(signature_html):
    """The firm's disclaimer paragraph out of the signature, as plain text ('' if it isn't there)."""
    plain = " ".join(H.unescape(re.sub(r"<[^>]+>", " ", signature_html)).replace("\xa0", " ").split())
    i = plain.find("Trading commodity futures")
    return plain[i:] if i >= 0 else ""


def config():
    """{chats, session, key} when WhatsApp is set up, else None."""
    chats = [c.strip() for c in (os.environ.get("RIVER_WA_CHATS") or "").split(",") if c.strip()]
    if not chats:
        return None
    return {"chats": chats, "session": os.environ.get("RIVER_WA_SESSION") or DEFAULT_SESSION, "key": os.environ.get("WA_API_KEY") or ""}


def _post(cfg, chats, payload):
    import requests
    try:
        r = requests.post(f"{BRIDGE}/send", json={"chat_ids": chats, **payload}, timeout=240,
                          headers={"x-api-key": cfg["key"], "x-session-id": cfg["session"]})
    except requests.ConnectionError as e:
        raise NotReady(f"the WhatsApp bridge isn't answering: {e}") from e
    if r.status_code == 503:
        raise NotReady(f"WhatsApp account '{cfg['session']}' isn't connected")
    if r.status_code != 200:
        raise RuntimeError(f"the WhatsApp bridge returned HTTP {r.status_code}")
    failed = [x for x in r.json().get("results", []) if not x.get("ok")]
    if failed:
        raise RuntimeError(f"WhatsApp refused {len(failed)} of {len(chats)} chats: {[x.get('error') for x in failed]}")


def send(cfg, chats, pngs, caption, closing=""):
    """The pictures in order, `caption` on the first, then `closing` as a text message."""
    if not cfg["key"]:
        raise NotReady("WA_API_KEY isn't set")
    _post(cfg, chats, {"message": caption, "images": [{"base64": base64.b64encode(p).decode(), "mime": "image/png"} for p in pngs]})
    if closing:
        try:
            _post(cfg, chats, {"message": closing})
        except NotReady as e:
            raise RuntimeError(f"the pictures posted but the closing text didn't: {e}") from e
