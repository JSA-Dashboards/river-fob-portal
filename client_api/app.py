"""River FOB client API (pilot): CIF NOLA, barge freight and FOB barge values, last 12 months, by API key.

    uvicorn client_api.app:app --host 127.0.0.1 --port 8000        # behind Caddy (HTTPS), see client_api/DEPLOY.md

Endpoints (all GET; every one but /v1/health needs the `X-API-Key` header):
    /v1/health                     {"status": "ok", "data": "ok"}  ("loading" for a few seconds after a restart)
    /v1/dates                      the saved sheet dates the key can see, newest first
    /v1/locations                  the 18 FOB locations (river, freight region) and the 7 freight regions
    /v1/cif      ?commodity=&start=&end=&format=
    /v1/freight  ?region=&start=&end=&format=
    /v1/fob      ?commodity=&location=&start=&end=&format=
commodity / location / region take one name or a comma-separated list (any case). start / end are YYYY-MM-DD and
inclusive; with neither, the newest sheet only. format = json (default) or csv. An unknown parameter is an error (a
typo never silently returns everything).

Deliberately small: fixed endpoints, no query language, no OpenAPI page (/docs is off; the client gets README.md), no
internals in an error, every call logged (who, what, how many rows), a rate limit per key.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import time
import datetime as dt

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from client_api import auth, data

log = logging.getLogger("river_fob_api")

FIELDS = {
    "cif": ["date", "commodity", "delivery_month", "month_label", "spot", "contract", "cif"],
    "freight": ["date", "region", "delivery_month", "month_label", "spot", "freight_pct"],
    "fob": ["date", "commodity", "location", "river", "freight_region", "delivery_month", "month_label", "spot",
            "contract", "fob"],
}
UNITS = {
    "cif": "cif = CIF NOLA basis in $/bu over the futures contract named in `contract` (0.93 = +93 cents).",
    "freight": "freight_pct = barge freight in percent of tariff (725 = 725%).",
    "fob": ("fob = FOB barge basis in $/bu over `contract`: CIF NOLA less barge freight "
            "(tariff rate x freight % / 2000 x bushel weight; corn 56 lb, soybeans and wheat 60)."),
}
ALLOWED = {"cif": {"commodity", "start", "end", "format"}, "freight": {"region", "start", "end", "format"},
           "fob": {"commodity", "location", "start", "end", "format"}, "dates": set(), "locations": set()}
_LOGGABLE = set().union(*ALLOWED.values())


def _log_query(params) -> str:
    """The query for the access log: our own parameters as sent, anything else as '***' (a client who puts the key in
    the URL by mistake must not have it written to the log)."""
    return "&".join(f"{k}={v if k in _LOGGABLE else '***'}" for k, v in params.multi_items())[:500]


def _names(raw: str | None, choices, what: str):
    """'corn,Soybeans' -> ['Corn', 'Soybeans'] (the canonical spelling); None for no filter; 400 for an unknown name."""
    if raw is None or not raw.strip():
        return None
    canon = {c.lower(): c for c in choices}
    out = []
    for part in raw.split(","):
        p = part.strip().lower()
        if p not in canon:
            raise HTTPException(400, f"Unknown {what} '{part.strip()}'. Choose from: {', '.join(choices)}.")
        out.append(canon[p])
    return out


def _day(raw: str | None, what: str):
    if raw is None:
        return None
    try:
        return dt.date.fromisoformat(raw.strip())
    except ValueError:
        raise HTTPException(400, f"{what} must be a date as YYYY-MM-DD, e.g. 2026-11-04.")


def create_app(store: auth.KeyStore | None = None, loader: data.Loader | None = None,
               limiter: auth.RateLimiter | None = None, access_log: str | None = os.environ.get("API_ACCESS_LOG"),
               today=None, warm: bool = False, calls: auth.CallLog | None = None) -> FastAPI:
    """`today` () -> date: the day the 12-month window ends (Central time by default; the tests pin it).
    `warm`: start loading the data now, in the background (the server does; the tests don't).
    `calls`: where every call is recorded for the 🔑 API tab (Snowflake by default; None in tests = not recorded)."""
    today = today or data.today_central
    store = store or auth.KeyStore(fetch=data.fetch_keys)
    loader = loader or data.Loader(today=today)
    limiter = limiter or auth.RateLimiter()
    if warm:
        loader.warm()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        # Shutdown (a restart, a deploy, a reboot): write the buffered calls NOW. uvicorn waits for this step; an atexit
        # handler ran too late to (found at go-live 2026-11-05: a restart lost the calls of the last 5-minute batch).
        if calls is not None:
            n = await asyncio.to_thread(calls.flush)
            log.warning("Shutdown: wrote %d buffered call record(s)", n)

    app = FastAPI(title="River FOB API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    alog = None
    if access_log:
        try:
            os.makedirs(os.path.dirname(access_log), exist_ok=True)
            alog = open(access_log, "a", encoding="utf-8", buffering=1)
        except OSError as e:
            log.warning("No access log (%s): %s", access_log, e)

    @app.middleware("http")
    async def wrap(request: Request, call_next):
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:                                         # noqa: BLE001 — never show a client a traceback
            log.exception("Unhandled error on %s", request.url.path)
            response = JSONResponse({"detail": "Internal error."}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        rec = getattr(request.state, "client", None) or {}
        fwd = request.headers.get("x-forwarded-for", "")
        now = dt.datetime.now(dt.timezone.utc)
        entry = {"ip": fwd.split(",")[0].strip() or (request.client.host if request.client else ""),
                 "key": rec.get("id"), "client": rec.get("client"), "path": request.url.path,
                 "query": _log_query(request.query_params), "status": response.status_code,
                 "rows": getattr(request.state, "rows", None), "ms": round((time.monotonic() - t0) * 1000)}
        if alog is not None:
            alog.write(json.dumps({"ts": now.isoformat(timespec="seconds"), **entry}) + "\n")
        # The 🔑 API tab tracks calls to the API's own paths only: not health probes, and not the internet's scanners
        # ('/', '/favicon.ico', '/.env' ... from the first hour live), which would clutter it and wake the warehouse every
        # 5 minutes all day. The local access log above still keeps everything.
        if calls is not None and request.url.path.startswith("/v1/") and request.url.path != "/v1/health":
            calls.add({"ts": now.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds"), "key_id": entry["key"],
                       "client": entry["client"], "path": entry["path"], "query": entry["query"], "status": entry["status"],
                       "rows_n": entry["rows"], "ms": entry["ms"], "ip": entry["ip"]})
        return response

    def guard(request: Request, name: str) -> dict:
        """The key (401), the rate limit (429), the parameters (400) — in that order."""
        rec = store.verify(request.headers.get("x-api-key"))
        if rec is None:
            raise HTTPException(401, "Missing or invalid API key (send it in the X-API-Key header).",
                                headers={"WWW-Authenticate": "ApiKey"})
        request.state.client = rec
        ok, wait = limiter.allow(rec["id"])
        if not ok:
            raise HTTPException(429, f"Rate limit reached; try again in {wait} seconds.", headers={"Retry-After": str(wait)})
        extra = sorted(set(request.query_params) - ALLOWED[name])
        if extra:
            raise HTTPException(400, f"Unknown parameter(s): {', '.join(extra)}. Allowed: {', '.join(sorted(ALLOWED[name])) or 'none'}.")
        return rec

    def dataset() -> dict:
        try:
            return loader.get()
        except Exception:                                         # noqa: BLE001
            log.exception("Data load failed")
            raise HTTPException(503, "Data temporarily unavailable; please try again shortly.")

    def respond(request: Request, name: str, rows: list, start, end, fmt: str | None):
        request.state.rows = len(rows)
        lo, hi = data.window(today())
        if (fmt or "json").lower() == "csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=FIELDS[name], extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: (str(v).lower() if isinstance(v, bool) else ("" if v is None else v)) for k, v in r.items()})
            return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="river_fob_{name}_{start}_{end}.csv"'})
        if (fmt or "json").lower() != "json":
            raise HTTPException(400, "format must be json or csv.")
        return {"dataset": name, "units": UNITS[name], "start": start, "end": end,
                "window": {"first": lo.isoformat(), "last": hi.isoformat()}, "count": len(rows), "rows": rows}

    def span(request: Request, ds: dict):
        """(start, end) as ISO strings: the newest sheet by default; clipped to the window; 400 if backwards."""
        start, end = _day(request.query_params.get("start"), "start"), _day(request.query_params.get("end"), "end")
        lo, hi = data.window(today())
        if start is None and end is None:
            newest = ds["dates"][0] if ds["dates"] else hi.isoformat()
            return newest, newest
        start, end = max(start or lo, lo), min(end or hi, hi)
        if start > end:
            raise HTTPException(400, "start is after end (or the range is outside the last 12 months).")
        return start.isoformat(), end.isoformat()

    def rows_for(request: Request, name: str, filters: dict):
        ds = dataset()
        start, end = span(request, ds)
        rows = [r for r in ds[name] if start <= r["date"] <= end
                and all(v is None or r[k] in v for k, v in filters.items())]
        return respond(request, name, rows, start, end, request.query_params.get("format"))

    @app.get("/v1/health")
    def health():
        """No key, nothing read: is the process up, and does it hold the data? `data` is "loading" until the first load
        lands (a few seconds after a restart). The uptime check (client_api/monitor/uptime.sh) needs "ok"."""
        return {"status": "ok", "data": "ok" if loader.loaded else "loading"}

    @app.get("/v1/dates")
    def dates(request: Request):
        guard(request, "dates")
        ds = dataset()
        request.state.rows = len(ds["dates"])
        lo, hi = data.window(today())
        return {"dates": ds["dates"], "window": {"first": lo.isoformat(), "last": hi.isoformat()}}

    @app.get("/v1/locations")
    def locations(request: Request):
        guard(request, "locations")
        return {"locations": [{"location": l.name, "river": l.reach, "freight_region": l.region} for l in data.LOCATIONS],
                "freight_regions": data.REGIONS, "commodities": list(data.COMMODITIES)}

    @app.get("/v1/cif")
    def cif(request: Request):
        guard(request, "cif")
        q = request.query_params
        return rows_for(request, "cif", {"commodity": _names(q.get("commodity"), data.COMMODITIES, "commodity")})

    @app.get("/v1/freight")
    def freight(request: Request):
        guard(request, "freight")
        return rows_for(request, "freight", {"region": _names(request.query_params.get("region"), data.REGIONS, "region")})

    @app.get("/v1/fob")
    def fob(request: Request):
        guard(request, "fob")
        q = request.query_params
        return rows_for(request, "fob", {"commodity": _names(q.get("commodity"), data.COMMODITIES, "commodity"),
                                          "location": _names(q.get("location"), [l.name for l in data.LOCATIONS], "location")})

    return app


if os.environ.get("API_WARM", "1") == "1":                        # the server: load at start-up, record calls in Snowflake
    app = create_app(warm=True, calls=auth.CallLog(write=data.write_calls))
else:                                                             # tests / tooling: nothing starts on import
    app = None
