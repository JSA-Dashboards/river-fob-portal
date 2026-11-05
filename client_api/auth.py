"""API keys and rate limits for the client API.

Keys:
  * One key per client group. Format 'rfob_' + 43 random URL-safe characters (secrets.token_urlsafe(32), 256 bits).
  * The server stores only its SHA-256 hash, in a JSON file OUTSIDE the repo (API_KEYS_FILE, default
    /etc/river-fob-api/keys.json, mode 600). A stolen file can't be turned back into a key, and a key this long can't be
    guessed, so a plain hash is enough (no bcrypt needed).
  * The file is re-read when it changes, so `manage_keys.py revoke` takes effect on the next call, with no restart.
  * Clients send it in the `X-API-Key` header, never in the URL (URLs end up in logs and browser history).

Rate limits (per key, in memory, one process): LIMIT_PER_MIN calls a minute and LIMIT_PER_DAY a day. The data changes
once a day, so these are generous for a client and stop a runaway script.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

KEY_PREFIX = "rfob_"
KEYS_FILE = os.environ.get("API_KEYS_FILE", "/etc/river-fob-api/keys.json")
LIMIT_PER_MIN = int(os.environ.get("API_LIMIT_PER_MIN", "60"))
LIMIT_PER_DAY = int(os.environ.get("API_LIMIT_PER_DAY", "5000"))


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class KeyStore:
    """{'keys': [{'id', 'client', 'sha256', 'created', 'active', 'note'}]} in a JSON file."""

    def __init__(self, path: str = KEYS_FILE):
        self.path = path
        self._keys, self._mtime = [], None
        self._lock = threading.Lock()

    def _load(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self._keys, self._mtime = [], None               # no file = no keys = every call refused
            return
        if mtime != self._mtime:
            with open(self.path, encoding="utf-8") as f:
                self._keys = json.load(f).get("keys", [])
            self._mtime = mtime

    def verify(self, raw: str | None) -> dict | None:
        """The active key record `raw` belongs to, else None. Every record is compared (constant-time), so the answer
        takes the same time whichever key, if any, matches."""
        if not raw or not raw.startswith(KEY_PREFIX) or len(raw) > 200:
            return None
        h = key_hash(raw)
        with self._lock:
            self._load()
            found = None
            for rec in self._keys:
                if hmac.compare_digest(h, str(rec.get("sha256", ""))) and rec.get("active", True):
                    found = rec
            return found

    # ── management (manage_keys.py) ──
    def _read_all(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"keys": []}

    def _write_all(self, data: dict):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def create(self, client: str, note: str = "") -> tuple[str, dict]:
        """A new key for `client`: returns (the raw key, shown ONCE, never stored; its record)."""
        data = self._read_all()
        raw = new_key()
        rec = {"id": "k_" + secrets.token_hex(4), "client": client, "sha256": key_hash(raw),
               "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "active": True, "note": note}
        data.setdefault("keys", []).append(rec)
        self._write_all(data)
        return raw, rec

    def revoke(self, key_id: str) -> bool:
        data = self._read_all()
        hit = False
        for rec in data.get("keys", []):
            if rec.get("id") == key_id and rec.get("active", True):
                rec["active"], rec["revoked"], hit = False, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), True
        if hit:
            self._write_all(data)
        return hit

    def list(self) -> list[dict]:
        return [{k: v for k, v in rec.items() if k != "sha256"} for rec in self._read_all().get("keys", [])]


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
