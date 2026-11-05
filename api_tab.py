"""🔑 API: manage the client API's keys and watch its calls (Kolten 2026-11-05: "make a tab on the River Fob admin dashboard
where I manage said APIs? A place to track the pulls, revoke access create others").

Shown only in this app's editable layouts (behind EDIT_PASSWORD), never in ?view=1, and switched off in the admin portal's
copy (`API_ADMIN_TAB = False` there, set by its sync). The keys and calls live in RIVER_FOB.API_ADMIN (api_admin.py has the
SQL and the rules); this module is only the page.

render(connect) — `connect()` -> (conn, placeholder): db._connect in the app; the tests hand in a SQLite one.
"""
from __future__ import annotations

import os
import time

import altair as alt
import pandas as pd
import streamlit as st

import api_admin

CENTRAL = "America/Chicago"
_CONNECT = None
_TABLES: dict = {}


def _api_url() -> str:
    """The API server's address, from the RIVER_FOB_API_URL secret (empty until the server is up)."""
    try:
        v = st.secrets.get("RIVER_FOB_API_URL", "")
    except Exception:                                        # noqa: BLE001 — no secrets file locally
        v = ""
    return (v or os.environ.get("RIVER_FOB_API_URL", "")).rstrip("/")


@st.cache_data(ttl=60, show_spinner=False)
def _load(version: int, days: int, client: str | None):
    """Keys, daily usage and recent calls in one connection; re-read at most every minute. A create / revoke sets `version`
    to the current time in ns (unique: a per-session counter could match another session's and hand back its older,
    cached list), so the page shows the change at once."""
    conn, ph = _CONNECT()
    try:
        kt = {k: v for k, v in _TABLES.items() if k == "keys_table"}
        ct = {k: v for k, v in _TABLES.items() if k == "calls_table"}
        return (api_admin.list_keys(conn, ph, days, **kt, **ct), api_admin.daily_usage(conn, ph, days, **ct),
                api_admin.recent_calls(conn, ph, days, client, **ct))
    finally:
        conn.close()


def _central(series) -> pd.Series:
    """UTC timestamps (datetime from Snowflake, text from SQLite) -> Central, for display."""
    s = pd.to_datetime(pd.Series(series), errors="coerce")
    s = s.dt.tz_localize("UTC") if s.dt.tz is None else s
    return s.dt.tz_convert(CENTRAL).dt.strftime("%b %d, %Y %I:%M %p")


def _when(v):
    """An expiry / time from the table (datetime from Snowflake, text from SQLite) -> naive UTC datetime, or None."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    t = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(t) else t.to_pydatetime().replace(tzinfo=None)


def _days_left(v):
    """Whole days until an expiry (negative once past); None when there is none (treated as expired, like the server)."""
    t = _when(v)
    if t is None:
        return None
    return int((t - api_admin._utcnow()).total_seconds() // 86400)


def _day(v) -> str:
    """A UTC time as its Central calendar date, 'May 05, 2027'."""
    t = _when(v)
    if t is None:
        return "—"
    return pd.Timestamp(t).tz_localize("UTC").tz_convert(CENTRAL).strftime("%b %d, %Y")


def _rerun():
    try:
        st.rerun(scope="fragment")
    except Exception:                                        # noqa: BLE001 — outside a fragment (tests), a full rerun
        st.rerun()


def _act(fn, *args, **kw):
    conn, ph = _CONNECT()
    try:
        return fn(conn, ph, *args, **{k: v for k, v in _TABLES.items() if k == "keys_table"}, **kw)
    finally:
        conn.close()


def render(connect, tables: dict | None = None):
    global _CONNECT, _TABLES
    _CONNECT, _TABLES = connect, dict(tables or {})
    st.markdown("### 🔑 Client API")
    url = _api_url()
    st.caption("Keys for the River FOB client API: CIF NOLA, barge freight and FOB values, the last 12 months of saved sheets. "
               f"A key is good for {api_admin.KEY_MONTHS} months, and **Extend** gives it {api_admin.KEY_MONTHS} more from today "
               "(the client keeps the same key). It's shown once when it's made; only its fingerprint is stored. A revoke "
               "reaches the API within about a minute."
               + (f" API address: `{url}`" if url else " (API address not set yet: the RIVER_FOB_API_URL secret.)"))
    st.session_state.setdefault("api_v", 0)

    # ── a key just made: shown once ───────────────────────────────────────────────────────────────────────────────────
    new = st.session_state.get("api_new_key")
    if new:
        with st.container(border=True):
            st.success(f"New key for **{new['client']}** ({new['key_id']}), good until {new.get('until', '?')}. "
                       "Copy it now: it won't be shown again.")
            st.code(new["raw"], language=None)
            st.caption("Send it through a password-manager share or a one-time secret link, never plain email or chat. "
                       "Give the client client_api/README.md with it.")
            if st.button("I've stored it — hide the key", key="api_hide_key", type="primary"):
                st.session_state.pop("api_new_key", None)
                _rerun()

    c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
    with c1:
        days = st.segmented_control("Period", [7, 30, 90], default=30, key="api_days",
                                    format_func=lambda d: f"{d} days") or 30
    try:
        keys, daily, _ = _load(st.session_state["api_v"], days, None)
    except Exception as e:                                   # noqa: BLE001
        st.warning(f"Couldn't read the API keys ({type(e).__name__}: {e}). Has client_api/sql/setup.sql step 1b been run, "
                   "and does this app's role have its grants?")
        return
    clients = sorted({k["client"] for k in keys})
    with c2:
        who = st.selectbox("Client", ["All clients"] + clients, key="api_client")
    client = None if who == "All clients" else who
    if client:
        keys_shown = [k for k in keys if k["client"] == client]
        _, daily, recent = _load(st.session_state["api_v"], days, client)
        daily = [d for d in daily if d["client"] == client]
    else:
        keys_shown = keys
        _, _, recent = _load(st.session_state["api_v"], days, None)

    # ── keys ───────────────────────────────────────────────────────────────────────────────────────────────────────────
    st.markdown("#### Keys")
    for k in keys:
        k["days_left"] = _days_left(k.get("expires_at"))
    soon = [k for k in keys if k["active"] and (k["days_left"] is None or k["days_left"] <= 30)]
    if soon:
        st.warning("**Expiring:** " + "; ".join(
            f"{k['client']} {k['key_id']} " + ("has EXPIRED (the API refuses it)" if k["days_left"] is None or k["days_left"] < 0
                                                else f"expires in {k['days_left']} day{'s' if k['days_left'] != 1 else ''}")
            for k in soon) + ". **Extend** keeps the client's key working; or make a new one and revoke the old.")
    if not keys_shown:
        st.info("No keys yet. Make one below.")
    else:
        df = pd.DataFrame(keys_shown)
        show = pd.DataFrame({
            "Client": df["client"], "Key ID": df["key_id"], "Key starts": df["key_hint"].fillna("") + "…",
            "Status": [("Revoked" if not a else "Expired" if (n is None or n < 0) else "Active")
                       for a, n in zip(df["active"], df["days_left"])],
            "Expires": [f"{_day(e)} ({'expired' if n is None or n < 0 else f'{n} d'})" if e is not None and str(e) != "NaT" else "—"
                        for e, n in zip(df["expires_at"], df["days_left"])],
            "Created": _central(df["created_at"]), "Last call": _central(df["last_call"]).fillna("never"),
            f"Calls ({days} d)": df["calls_n"], f"Rows served ({days} d)": df["rows_total"],
            "Revoked": _central(df["revoked_at"]).fillna(""), "Note": df["note"].fillna("")})
        st.dataframe(show, hide_index=True, width="stretch")

    k1, k3, k2 = st.columns(3)
    with k1:
        with st.form("api_create", clear_on_submit=True, border=True):
            st.markdown("**Create a key**")
            name = st.text_input("Client group", placeholder="e.g. Hertshten")
            note = st.text_input("Note (optional)", placeholder="who asked, how it was sent")
            if st.form_submit_button("Create key", type="primary"):
                if not name.strip():
                    st.error("Enter the client group's name.")
                else:
                    raw, rec = _act(api_admin.create_key, name, note, by="river-fob app")
                    st.session_state["api_new_key"] = {"raw": raw, "client": rec["client"], "key_id": rec["key_id"],
                                                       "until": _day(rec["expires_at"])}
                    st.session_state["api_v"] = time.time_ns()
                    _rerun()
    with k3:
        with st.container(border=True):
            st.markdown(f"**Extend a key {api_admin.KEY_MONTHS} months**")
            live = [k for k in keys if k["active"]]
            if not live:
                st.caption("No active keys.")
            else:
                ext = st.selectbox("Key", live, key="api_extend_pick",
                                   format_func=lambda k: f"{k['client']} — {k['key_id']} (until {_day(k.get('expires_at'))})")
                to = _day(api_admin.add_months(api_admin._utcnow(), api_admin.KEY_MONTHS))
                if st.button(f"Extend to {to}", key="api_extend"):
                    _act(api_admin.extend_key, ext["key_id"])
                    st.session_state["api_v"] = time.time_ns()
                    st.toast(f"{ext['client']} {ext['key_id']} is good until {to}.")
                    _rerun()
    with k2:
        with st.container(border=True):
            st.markdown("**Revoke a key**")
            active = [k for k in keys if k["active"]]
            if not active:
                st.caption("No active keys.")
            else:
                pick = st.selectbox("Key", active, key="api_revoke_pick",
                                    format_func=lambda k: f"{k['client']} — {k['key_id']} ({k['key_hint']}…)")
                sure = st.checkbox(f"Yes: {pick['client']} loses access with this key within about a minute",
                                   key=f"api_revoke_ok_{pick['key_id']}")
                if st.button("Revoke", key="api_revoke", disabled=not sure):
                    _act(api_admin.revoke_key, pick["key_id"], by="river-fob app")
                    st.session_state["api_v"] = time.time_ns()
                    st.toast(f"Revoked {pick['key_id']} ({pick['client']}).")
                    _rerun()

    # ── usage ──────────────────────────────────────────────────────────────────────────────────────────────────────────
    st.markdown("#### Calls")
    if not daily:
        st.caption(f"No calls in the last {days} days." + ("" if url else " (The API server isn't live yet.)"))
    else:
        d = pd.DataFrame(daily)
        d["day"] = pd.to_datetime(d["day"])
        chart = alt.Chart(d).mark_bar().encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(format="%b %d")),
            y=alt.Y("calls_n:Q", title="calls"),
            color=alt.Color("client:N", title=None, legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("day:T", format="%b %d, %Y"), "client:N", alt.Tooltip("calls_n:Q", title="calls"),
                     alt.Tooltip("rows_total:Q", title="rows served", format=",")]
        ).properties(height=220)
        st.altair_chart(chart, width="stretch")
        refused = sum(int(x["calls_n"]) for x in daily if x["client"] == "(no valid key)")
        if refused:
            st.caption(f"{refused} call(s) in this period had no valid key (a typo, a revoked key, or someone probing). "
                       "They're refused and listed below.")
    if recent:
        r = pd.DataFrame(recent)
        st.dataframe(pd.DataFrame({
            "When (Central)": _central(r["ts"]), "Client": r["client"].fillna("(no valid key)"), "Key ID": r["key_id"].fillna(""),
            "Endpoint": r["path"], "Parameters": r["query"].fillna(""), "Status": r["status"], "Rows": r["rows_n"],
            "ms": r["ms"], "IP": r["ip"]}), hide_index=True, width="stretch", height=min(38 * (len(r) + 1), 420))
        st.caption("Calls reach this list within about 5 minutes (the API writes them in batches).")

    # ── the server ─────────────────────────────────────────────────────────────────────────────────────────────────────
    if url and st.button("Check the API server", key="api_health"):
        import requests
        try:
            resp = requests.get(url + "/v1/health", timeout=8)
            ok = resp.status_code == 200 and resp.json().get("status") == "ok"
            (st.success if ok else st.error)(f"{url}/v1/health → {resp.status_code} in {resp.elapsed.total_seconds() * 1000:.0f} ms")
        except Exception as e:                               # noqa: BLE001
            st.error(f"Couldn't reach {url} ({type(e).__name__}: {e}).")
