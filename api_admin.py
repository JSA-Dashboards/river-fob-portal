"""Client API keys and their call log, for River FOB's 🔑 API tab (and the command line, as a fallback).

Kolten 2026-11-05: "make a tab on the River Fob admin dashboard where I manage said APIs? A place to track the pulls, revoke
access, create others". The keys and the call log live in RIVER_FOB.API_ADMIN (client_api/sql/setup.sql, step 1b), so the
tab on Streamlit Cloud and the API server meet in Snowflake: the server reads active key HASHES and appends calls, and has no
admin endpoint of its own on the internet.

A key is 'rfob_' + 256 random bits. It's shown ONCE when made and only its SHA-256 hash is stored, so a lost key can't be
recovered: revoke it and make another. A revoke reaches the server within about a minute (it re-reads the active keys at
most once a minute). Times are stored in UTC.

    python api_admin.py list                      # from the PC, with River FOB's .env (Snowflake)
    python api_admin.py create "Hertshten" --note "pilot"
    python api_admin.py revoke k_1a2b3c4d
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

KEY_PREFIX = "rfob_"
KEYS = "RIVER_FOB.API_ADMIN.API_KEYS"
CALLS = "RIVER_FOB.API_ADMIN.API_CALLS"


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(raw: str) -> str:
    """Must equal client_api.auth.key_hash (the server checks keys with it); tests/test_client_api.py checks they agree."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def create_key(conn, ph: str, client: str, note: str = "", by: str = "river-fob app",
               keys_table: str = KEYS) -> tuple[str, dict]:
    """Make a key for `client`. Returns (the raw key, to show once; its record). Nothing raw is stored."""
    client = (client or "").strip()
    if not client:
        raise ValueError("A client group name is needed.")
    raw = new_key()
    rec = {"key_id": "k_" + secrets.token_hex(4), "client": client, "key_hash": key_hash(raw), "key_hint": raw[:9],
           "note": (note or "").strip(), "created_at": _now(), "created_by": by}
    cur = conn.cursor()
    cur.execute(f"INSERT INTO {keys_table} (key_id, client, key_hash, key_hint, note, created_at, created_by, active) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, TRUE)",
                (rec["key_id"], rec["client"], rec["key_hash"], rec["key_hint"], rec["note"], rec["created_at"], rec["created_by"]))
    conn.commit()
    return raw, rec


def revoke_key(conn, ph: str, key_id: str, by: str = "river-fob app", keys_table: str = KEYS) -> bool:
    """Revoke an active key. True if one was revoked."""
    cur = conn.cursor()
    cur.execute(f"UPDATE {keys_table} SET active = FALSE, revoked_at = {ph}, revoked_by = {ph} WHERE key_id = {ph} AND active",
                (_now(), by, key_id))
    n = cur.rowcount
    conn.commit()
    return bool(n)


def _rows(cur) -> list[dict]:
    cols = [c[0].lower() for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def list_keys(conn, ph: str, days: int = 30, keys_table: str = KEYS, calls_table: str = CALLS) -> list[dict]:
    """Every key, newest first, with its last call and its calls / rows served in the last `days` days."""
    since = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    cur = conn.cursor()
    cur.execute(f"SELECT key_id, client, key_hint, note, created_at, created_by, active, revoked_at, revoked_by "
                f"FROM {keys_table} ORDER BY created_at DESC")
    keys = _rows(cur)
    cur.execute(f"SELECT key_id, MAX(ts) AS last_call, "
                f"SUM(CASE WHEN ts >= {ph} THEN 1 ELSE 0 END) AS calls_n, "
                f"SUM(CASE WHEN ts >= {ph} AND status = 200 THEN COALESCE(rows_n, 0) ELSE 0 END) AS rows_total "
                f"FROM {calls_table} WHERE key_id IS NOT NULL GROUP BY key_id", (since, since))
    use = {r["key_id"]: r for r in _rows(cur)}
    for k in keys:
        u = use.get(k["key_id"], {})
        k["last_call"], k["calls_n"], k["rows_total"] = u.get("last_call"), int(u.get("calls_n") or 0), int(u.get("rows_total") or 0)
    return keys


def recent_calls(conn, ph: str, days: int = 30, client: str | None = None, limit: int = 500,
                 calls_table: str = CALLS) -> list[dict]:
    """The latest calls (newest first) within `days` days, optionally one client's (refused calls carry no client)."""
    since = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    sql = f"SELECT ts, key_id, client, path, query, status, rows_n, ms, ip FROM {calls_table} WHERE ts >= {ph}"
    args = [since]
    if client:
        sql += f" AND client = {ph}"
        args.append(client)
    cur = conn.cursor()
    cur.execute(sql + f" ORDER BY ts DESC LIMIT {int(limit)}", tuple(args))
    return _rows(cur)


def daily_usage(conn, ph: str, days: int = 30, calls_table: str = CALLS) -> list[dict]:
    """Calls per UTC day and client (refused calls with no key as '(no valid key)'), for the chart."""
    since = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    cur = conn.cursor()
    cur.execute(f"SELECT DATE(ts) AS day, COALESCE(client, '(no valid key)') AS client, COUNT(*) AS calls_n, "
                f"SUM(CASE WHEN status = 200 THEN COALESCE(rows_n, 0) ELSE 0 END) AS rows_total "
                f"FROM {calls_table} WHERE ts >= {ph} GROUP BY DATE(ts), COALESCE(client, '(no valid key)') ORDER BY 1", (since,))
    return _rows(cur)


if __name__ == "__main__":
    import argparse
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(here, ".env"), override=True)
    sys.path.insert(0, here)
    import db
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("client")
    c.add_argument("--note", default="")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("key_id")
    a = ap.parse_args()
    if db._backend() != "snowflake":
        sys.exit("refusing: not on Snowflake (check this repo's .env)")
    conn, ph = db._connect()
    try:
        if a.cmd == "create":
            raw, rec = create_key(conn, ph, a.client, a.note, by="command line")
            print(f"Created {rec['key_id']} for {rec['client']}. The key, shown once:\n\n    {raw}\n")
        elif a.cmd == "list":
            for k in list_keys(conn, ph):
                state = "active" if k["active"] else f"REVOKED {k['revoked_at']}"
                print(f"{k['key_id']}  {k['client']}  {k['key_hint']}…  created {k['created_at']}  {state}  "
                      f"last call {k['last_call'] or '-'}  {k['calls_n']} calls / 30 d")
        else:
            print(f"Revoked {a.key_id}." if revoke_key(conn, ph, a.key_id, by="command line") else f"No active key {a.key_id}.")
    finally:
        conn.close()
