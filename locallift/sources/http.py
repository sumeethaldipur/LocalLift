"""Outbound HTTP for source adapters: host allow-list, per-host pacing, bounded retries, request log."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

import httpx

from .. import config, db
from ..timeutil import iso, now_utc

# Hosts the fixed adapters may call. fetchApprovedSource uses config.approved_hosts.
ADAPTER_HOSTS = {
    "app.ticketmaster.com", "data.cityofnewyork.us", "gothamist.com", "api.gdeltproject.org", "news.google.com",
    "api.bsky.app", "public.api.bsky.app", "bsky.social", "api.x.com", "api-endpoint.mta.info", "www.wikidata.org",
}

# Minimum seconds between requests per host. GDELT documents 1 req / 5 s, but from this network it
# throttled far more aggressively in testing (about 1 success every few minutes), so pace at 20 s.
MIN_INTERVAL = {"api.gdeltproject.org": 20.0, "api.bsky.app": 2.0, "app.ticketmaster.com": 0.25, "api.x.com": 1.0}

_last_call: dict[str, float] = {}
_pace_lock = threading.Lock()

SECRET_PARAMS = {"apikey", "api_key", "key", "token", "access_token"}


class SourceError(Exception):
    def __init__(self, message: str, status: int | None = None, kind: str = "error", retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.kind = kind  # error | rate_limited | auth | forbidden | unavailable | not_allowed
        self.retry_after = retry_after


def redact(url: str) -> str:
    p = urlparse(url)
    qs = [(k, "REDACTED" if k.lower() in SECRET_PARAMS else v) for k, v in parse_qsl(p.query)]
    return urlunparse(p._replace(query=urlencode(qs)))


def _pace(host: str) -> None:
    gap = MIN_INTERVAL.get(host, 0)
    if not gap:
        return
    with _pace_lock:
        wait = _last_call.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def _log(provider: str, url: str, started: float, status: int | None, ok: bool, error: str | None, cost: float = 0):
    try:
        db.ex("INSERT INTO request_log(provider,url,started_at,status,ok,ms,error,cost_usd) VALUES(?,?,?,?,?,?,?,?)",
              (provider, redact(url)[:500], iso(now_utc()), status, int(ok), int((time.monotonic() - started) * 1000),
               (error or "")[:500], cost))
    except Exception:
        pass


def request(provider: str, method: str, url: str, *, params: dict | None = None, headers: dict | None = None,
            json_body: dict | None = None, body: bytes | None = None, allowed_hosts: set[str] | None = None, timeout: float = 20,
            retries: int = 2, cost_usd: float = 0) -> httpx.Response:
    full = str(httpx.URL(url, params=params)) if params else url
    host = (urlparse(full).hostname or "").lower()
    allowed = allowed_hosts if allowed_hosts is not None else ADAPTER_HOSTS
    if host not in allowed:
        raise SourceError(f"Host {host} is not on the approved list", kind="not_allowed")
    hdrs = {"User-Agent": f"LocalLift/0.1 ({config.settings.contact})", "Accept": "*/*"}
    hdrs.update(headers or {})
    last_exc: SourceError | None = None
    for attempt in range(retries + 1):
        _pace(host)
        started = time.monotonic()
        try:
            r = httpx.request(method, full, headers=hdrs, json=json_body, content=body, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as e:
            _log(provider, full, started, None, False, str(e))
            last_exc = SourceError(f"Network error: {e.__class__.__name__}", kind="unavailable")
            time.sleep(min(2 ** attempt, 4))
            continue
        ok = r.status_code < 400
        _log(provider, full, started, r.status_code, ok, None if ok else r.text[:200], cost_usd if ok else 0)
        if ok:
            return r
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            ra_s = float(ra) if ra and re.fullmatch(r"\d+(\.\d+)?", ra) else None
            last_exc = SourceError("Rate limited by provider", 429, "rate_limited", ra_s)
            if ra_s and ra_s > 10:
                break  # respect long backoff; let the scheduler try later
            time.sleep(ra_s or min(5 * (attempt + 1), 10))
            continue
        if r.status_code in (401,):
            raise SourceError("Authentication failed or credential missing", 401, "auth")
        if r.status_code == 403:
            raise SourceError(f"Access forbidden: {r.text[:160]}", 403, "forbidden")
        if r.status_code >= 500:
            last_exc = SourceError(f"Provider error {r.status_code}", r.status_code, "unavailable")
            time.sleep(min(2 ** attempt, 4))
            continue
        raise SourceError(f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
    raise last_exc or SourceError("Request failed")


def set_status(provider: str, category: str, status: str, detail: str = "", credential_status: str = "",
               success: bool = False, next_step: str = "") -> None:
    ts = iso(now_utc())
    prev = db.q1("SELECT last_success FROM source_status WHERE provider=?", (provider,))
    last_success = ts if success else (prev["last_success"] if prev else None)
    db.ex("""INSERT INTO source_status(provider,category,status,detail,credential_status,last_attempt,last_success,next_step)
             VALUES(?,?,?,?,?,?,?,?)
             ON CONFLICT(provider) DO UPDATE SET category=excluded.category,status=excluded.status,detail=excluded.detail,
             credential_status=excluded.credential_status,last_attempt=excluded.last_attempt,
             last_success=excluded.last_success,next_step=excluded.next_step""",
          (provider, category, status, detail[:600], credential_status, ts, last_success, next_step))


def status_from_error(e: SourceError) -> str:
    return {"rate_limited": "rate_limited", "auth": "setup_required", "forbidden": "access_limited",
            "unavailable": "unavailable", "not_allowed": "unavailable"}.get(e.kind, "unavailable")
