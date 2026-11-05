"""API keys, the call log and rate limits for the client API.

Keys (managed in River FOB's 🔑 API tab, api_admin.py; Kolten 2026-11-05):
  * One key per client group: 'rfob_' + 43 random URL-safe characters (256 bits), sent in the `X-API-Key` header, never
    in the URL (URLs end up in logs and browser history).
  * Snowflake holds only SHA-256 hashes. The server reads ACTIVE ones through RIVER_FOB.API_ADMIN.ACTIVE_KEYS (a key this
    long can't be guessed, so a plain hash is enough; no bcrypt needed).
  * KeyStore caches that list. Older than REFRESH_S (60 s) while calls come in: re-read in the background, so a revoke in
    the tab reaches the server within about a minute. Older than BLOCK_S (5 min, e.g. after a quiet night): re-read BEFORE
    answering, so a key revoked while nobody was calling is refused at once. No successful re-read for MAX_STALE_S (24 h):
    every key is refused (fail closed).
  * The re-read is the same plain SELECT each time, so Snowflake's result cache answers it without waking a warehouse
    unless a key changed.

Call log: every call (refused ones too) goes to the local access log at once, and into a buffer that CallLog writes to
RIVER_FOB.API_ADMIN.API_CALLS in one INSERT every FLUSH_S (5 min), only when there were calls, so the tab's usage view
costs a warehouse minute per busy five minutes at most.

Rate limits (per key, in memory, one process): LIMIT_PER_MIN a minute, LIMIT_PER_DAY a day. The data changes once a day.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import hmac
import logging
import os
import threading
import time

KEY_PREFIX = "rfob_"
REFRESH_S, BLOCK_S, MAX_STALE_S = 60, 300, 86400
FLUSH_S, MAX_BUFFER = 300, 10000
LIMIT_PER_MIN = int(os.environ.get("API_LIMIT_PER_MIN", "60"))
LIMIT_PER_DAY = int(os.environ.get("API_LIMIT_PER_DAY", "5000"))
log = logging.getLogger("river_fob_api")


def key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _when(v):
    """An expiry as the view hands it (a naive UTC datetime from Snowflake, text from SQLite) -> datetime, or None."""
    if v is None or isinstance(v, dt.datetime):
        return v
    try:
        return dt.datetime.fromisoformat(str(v))
    except ValueError:
        return None


class KeyStore:
    """The active keys, from `fetch()` -> [{'key_id', 'client', 'key_hash', 'expires_at'}], cached as described above.
    A key is good until its `expires_at` (UTC; 6 months from when it was made, api_admin.KEY_MONTHS): after that verify()
    hands back the record marked expired, so the caller can say so, and the key opens nothing. A key with no expiry (none
    should exist: the table was back-filled when expiry came in) is treated as expired, failing closed."""

    def __init__(self, fetch, clock=time.monotonic, refresh=REFRESH_S, block=BLOCK_S, max_stale=MAX_STALE_S, now=_utcnow):
        self.fetch, self.clock, self.now = fetch, clock, now
        self.refresh, self.block, self.max_stale = refresh, block, max_stale
        self._keys, self._at = None, None
        self._busy = False
        self._lock = threading.Lock()

    def _load(self):
        try:
            keys = [dict(k) for k in self.fetch()]
        except Exception:                                    # noqa: BLE001 — keep the last list; logged for the server's journal
            log.exception("Couldn't re-read the API keys")
            with self._lock:
                self._busy = False
            return
        with self._lock:
            self._keys, self._at, self._busy = keys, self.clock(), False

    def _current(self):
        with self._lock:
            age = None if self._at is None else self.clock() - self._at
            if age is not None and age > self.refresh and age <= self.block and not self._busy:
                self._busy = True
                threading.Thread(target=self._load, daemon=True, name="river-fob-api-keys").start()
            stale = age is None or age > self.block
        if stale:
            self._load()                                     # first use, or quiet for 5 min: re-read before answering
        with self._lock:
            if self._at is None or self.clock() - self._at > self.max_stale:
                return []                                    # never read, or not re-read for a day: refuse every key
            return self._keys or []

    def verify(self, raw: str | None) -> dict | None:
        """The active key record `raw` belongs to — {'id', 'client', 'expires_at', 'expired'} — else None. Every record is
        compared (constant time). The caller must refuse a record with expired=True."""
        if not raw or not raw.startswith(KEY_PREFIX) or len(raw) > 200:
            return None
        h = key_hash(raw)
        found = None
        for rec in self._current():
            if hmac.compare_digest(h, str(rec.get("key_hash", ""))):
                until = _when(rec.get("expires_at"))
                found = {"id": rec.get("key_id"), "client": rec.get("client"), "expires_at": until,
                         "expired": until is None or until <= self.now()}
        return found


class CallLog:
    """Buffers call records and writes them with `write(rows)` every `every` seconds, from one background thread. A failed
    write keeps the rows for the next try; past MAX_BUFFER the oldest are dropped (and counted), so a flood of refused calls
    can't grow memory without bound."""

    def __init__(self, write, every: int = FLUSH_S, max_buffer: int = MAX_BUFFER, start: bool = True):
        self.write, self.every, self.max_buffer = write, every, max_buffer
        self._buf, self.dropped = collections.deque(), 0
        self._lock = threading.Lock()
        if start:
            threading.Thread(target=self._loop, daemon=True, name="river-fob-api-calls").start()

    def add(self, rec: dict):
        with self._lock:
            if len(self._buf) >= self.max_buffer:
                self._buf.popleft()
                self.dropped += 1
            self._buf.append(rec)

    def flush(self) -> int:
        with self._lock:
            rows = list(self._buf)
            self._buf.clear()
        if not rows:
            return 0
        try:
            self.write(rows)
            return len(rows)
        except Exception:                                    # noqa: BLE001 — put them back for the next try
            log.exception("Couldn't write %d call records; keeping them", len(rows))
            with self._lock:
                for r in reversed(rows):
                    if len(self._buf) < self.max_buffer:
                        self._buf.appendleft(r)
            return 0

    def _loop(self):
        while True:
            time.sleep(self.every)
            self.flush()


class RateLimiter:
    """Sliding windows per key id: at most `per_min` calls in any 60 s and `per_day` in any 24 h."""

    def __init__(self, per_min: int = LIMIT_PER_MIN, per_day: int = LIMIT_PER_DAY, clock=time.monotonic):
        self.per_min, self.per_day, self.clock = per_min, per_day, clock
        self._calls = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key_id: str) -> tuple[bool, int]:
        """(allowed, seconds to wait if not)."""
        now = self.clock()
        with self._lock:
            q = self._calls[key_id]
            while q and now - q[0] >= 86400:
                q.popleft()
            last_min = sum(1 for t in q if now - t < 60)
            if len(q) >= self.per_day:
                return False, int(86400 - (now - q[0])) + 1
            if last_min >= self.per_min:
                oldest = next(t for t in q if now - t < 60)
                return False, int(60 - (now - oldest)) + 1
            q.append(now)
            return True, 0
