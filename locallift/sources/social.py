"""Social adapters: Bluesky (free) and Twitter/X (pay-per-use, disabled unless configured).

Only aggregate observations, post IDs and a few brief attributed excerpts are kept.
Likes, mentions and authors are attention signals, never attendance or ticket sales.
Absence of sampled posts is not evidence of no demand.
"""
from __future__ import annotations

import os

import hashlib
import re
from datetime import timedelta

from .. import config, db
from ..timeutil import iso, now_utc, parse
from .http import SourceError, request, set_status, status_from_error

BSKY_APPVIEW = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"  # documented AppView host
BSKY_PUBLIC = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
BSKY_PDS = "https://bsky.social/xrpc"
BSKY_MAX_PAGES = 2
X_SEARCH = "https://api.x.com/2/tweets/search/recent"
X_COUNTS = "https://api.x.com/2/tweets/counts/recent"

NEED_PATTERNS = {
    "quick food before the event": r"\b(eat|food|dinner|grab a bite|hungry|pre[- ]?game|before the (show|game|concert))\b",
    "coffee / caffeine": r"\b(coffee|latte|espresso|caffeine|cold brew)\b",
    "meeting place": r"\b(meet (up|at)|meeting spot|where to meet|meetup)\b",
    "late-night options": r"\b(late night|after the (show|game|concert)|open late)\b",
    "waiting / killing time": r"\b(early|kill time|waiting|doors open|line)\b",
    "transit disruption": r"\b(delay|delayed|penn station|lirr|nj transit|amtrak|subway|shuttle)\b",
}

GEO_TERMS = re.compile(r"\b(nyc|new york|manhattan|msg|madison square|penn station|midtown|hulu theater|"
                       r"the garden|brooklyn|queens|chelsea|hudson yards)\b", re.I)

_bsky_session: dict = {}


def _norm_text(t: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", (t or "").lower()).strip()[:200].encode()).hexdigest()[:12]


def extract_needs(texts: list[str]) -> dict[str, int]:
    out = {}
    for label, pat in NEED_PATTERNS.items():
        n = sum(1 for t in texts if re.search(pat, t or "", re.I))
        if n:
            out[label] = n
    return out


def _bsky_auth_headers() -> tuple[str, dict]:
    s = config.settings
    if not (s.bsky_handle and s.bsky_password):
        return BSKY_APPVIEW, {}
    if not _bsky_session.get("accessJwt"):
        r = request("bluesky", "POST", f"{BSKY_PDS}/com.atproto.server.createSession",
                    json_body={"identifier": s.bsky_handle, "password": s.bsky_password}, retries=0)
        _bsky_session.update(r.json())
    return f"{BSKY_PDS}/app.bsky.feed.searchPosts", {"Authorization": f"Bearer {_bsky_session['accessJwt']}"}


def _bsky_window(q: str, since, until) -> dict:
    url, headers = _bsky_auth_headers()
    posts, cursor, pages, truncated = [], None, 0, False
    while True:
        params = {"q": q, "sort": "latest", "limit": 100, "since": iso(since), "until": iso(until)}
        if cursor:
            params["cursor"] = cursor
            try:
                data = request("bluesky", "GET", url, params=params, headers=headers, retries=0).json()
            except SourceError as e:
                if e.kind != "forbidden":
                    raise
                # Unauthenticated search refuses deep pagination; keep page 1 and mark the sample truncated.
                truncated = True
                break
        else:
            data = request("bluesky", "GET", url, params=params, headers=headers).json()
        posts.extend(data.get("posts", []))
        pages += 1
        cursor = data.get("cursor")
        if not cursor or not data.get("posts"):
            break
        if pages >= BSKY_MAX_PAGES:
            truncated = True
            break
    return {"posts": posts, "pages": pages, "truncated": truncated}


def _summarize(posts: list[dict], provider: str) -> dict:
    seen_ids, seen_text, authors, excerpts, texts = set(), set(), set(), [], []
    likes = reposts = 0
    for p in posts:
        pid = p["id"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        th = _norm_text(p["text"])
        if th in seen_text:  # copy/paste or syndicated duplicate: not independent
            continue
        seen_text.add(th)
        authors.add(p["author"])
        texts.append(p["text"])
        likes += p.get("likes", 0)
        reposts += p.get("reposts", 0)
        if len(excerpts) < 5:
            excerpts.append({"text": p["text"][:140] + ("…" if len(p["text"]) > 140 else ""),
                             "author": p.get("handle"), "url": p.get("url"), "created_at": p.get("created_at")})
    geo = sum(1 for t in texts if GEO_TERMS.search(t or ""))
    return {"unique_posts": len(seen_text), "unique_authors": len(authors), "post_ids": sorted(seen_ids)[:500],
            "excerpts": excerpts, "needs": extract_needs(texts), "geo_specific_posts": geo,
            "metrics": {"likes": likes, "reposts": reposts, "provider": provider}}


def bluesky_search(q: str, hours: int = 24) -> dict:
    """Two comparable windows (current and prior, same query/provider/page bound)."""
    now = now_utc().replace(microsecond=0)
    windows = {}
    try:
        for name, (a, b) in {"current": (now - timedelta(hours=hours), now),
                             "prior": (now - timedelta(hours=2 * hours), now - timedelta(hours=hours))}.items():
            w = _bsky_window(q, a, b)
            norm = []
            for p in w["posts"]:
                rec = p.get("record") or {}
                handle = (p.get("author") or {}).get("handle")
                rkey = p.get("uri", "").rsplit("/", 1)[-1]
                norm.append({"id": p.get("uri"), "text": rec.get("text", ""), "author": (p.get("author") or {}).get("did"),
                             "handle": handle, "created_at": rec.get("createdAt"),
                             "likes": p.get("likeCount", 0), "reposts": p.get("repostCount", 0),
                             "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if handle else None})
            summ = _summarize(norm, "bluesky")
            # Posting rate: a truncated sample only covers from its oldest post to the window end.
            times = [parse(p["created_at"]) for p in norm if p.get("created_at")]
            times = [t for t in times if t and a <= t <= b]
            covered = ((b - min(times)).total_seconds() / 3600) if (w["truncated"] and times) else hours
            covered = max(covered, 0.25)
            windows[name] = {"start": iso(a), "end": iso(b), "pages": w["pages"], "truncated": w["truncated"],
                             "sample_size": len(norm), "covered_hours": round(covered, 2),
                             "rate_per_hour": round(summ["unique_posts"] / covered, 2), **summ}
    except SourceError as e:
        set_status("bluesky", "social", status_from_error(e), str(e),
                   "app_password" if config.settings.bsky_handle else "unauthenticated",
                   next_step="If search is refused, set BSKY_HANDLE and BSKY_APP_PASSWORD (app password).")
        raise
    set_status("bluesky", "social", "healthy",
               f"Search via {'authenticated PDS' if config.settings.bsky_handle else 'api.bsky.app (unauthenticated)'}; "
               "public.api.bsky.app returned 403 for searchPosts when tested.",
               "app_password" if config.settings.bsky_handle else "unauthenticated", success=True)
    return {"provider": "bluesky", "query": q, "windows": windows, "trend": trend(windows)}


def trend(windows: dict) -> dict:
    """Compare two equal-length windows with the same query and provider.
    Complete windows compare post counts. When the unauthenticated API returns only the newest page, we compare
    posting *rates* (posts per hour over the span the sample actually covers) — the same estimator in both windows."""
    c, p = windows.get("current"), windows.get("prior")
    if not c or not p:
        return {"label": "Trend unavailable", "reason": "Only one collection window."}
    a, b = c["unique_posts"], p["unique_posts"]
    if a + b < 5:
        return {"label": "Trend unavailable", "reason": f"Too few posts ({a} vs {b}) to compare."}
    rate_based = c.get("truncated") or p.get("truncated")
    if rate_based:
        if "rate_per_hour" not in c or "rate_per_hour" not in p:
            return {"label": "Trend unavailable", "reason": "Sample truncated; no posting-rate data."}
        x, y = c["rate_per_hour"], p["rate_per_hour"]
    else:
        x, y = a, b
    if y == 0:
        lab = "Rising" if x > 0 else "Steady"
    else:
        lab = "Rising" if x >= y * 1.5 else "Falling" if x <= y / 1.5 else "Steady"
    change = None if y == 0 else round((x - y) / y * 100)
    return {"label": lab, "current": x, "prior": y, "change_pct": change,
            "method": "posting rate (posts/hour, newest-page sample)" if rate_based else "post counts, complete windows",
            "reason": ("Both windows sampled the newest posts; compared posts per hour over the span each sample covers."
                       if rate_based else "Same query, provider and full windows.")}


# ------------------------------------------------------------------ Twitter / X

def x_month_spend() -> float:
    start = now_utc().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    r = db.q1("SELECT COALESCE(SUM(cost_usd),0) s FROM request_log WHERE provider='x' AND started_at>=?", (iso(start),))
    return float(r["s"]) if r else 0.0


def x_cycle_reads() -> int | None:
    """Posts read this billing cycle, from X's own usage endpoint (free, reads no posts). Shared across every server,
    unlike the local request log, so it holds the cap on hosted deployments. Cached for two minutes."""
    if _x_usage.get("at") and (now_utc() - _x_usage["at"]).total_seconds() < 120:
        return _x_usage["reads"]
    try:
        r = request("x", "GET", X_USAGE, retries=0, headers={"Authorization": f"Bearer {x_bearer_token()}"})
        reads = int((r.json().get("data") or {}).get("project_usage"))
    except (SourceError, TypeError, ValueError):
        reads = None
    _x_usage.update(at=now_utc(), reads=reads)
    return reads


def x_spent() -> float:
    """Best estimate of this cycle's X spend: the larger of the local ledger and X's own read count (plus local
    counts-call estimates). On Vercel, if X's count can't be read, fail closed rather than guess."""
    local = x_month_spend()
    reads = x_cycle_reads()
    if reads is None:
        return float("inf") if os.getenv("VERCEL") else local
    start = now_utc().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    counts = db.q1("SELECT COALESCE(SUM(cost_usd),0) s FROM request_log WHERE provider='x' AND url LIKE '%/counts/%' "
                   "AND started_at>=?", (iso(start),))
    return max(local, reads * config.settings.x_cost_per_read + float(counts["s"] if counts else 0))


def x_daily_calls_left() -> bool:
    lim = config.settings.x_daily_call_limit
    if lim <= 0:
        return True
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    r = db.q1("SELECT COUNT(*) c FROM request_log WHERE provider='x' AND started_at>=? "
              "AND (url LIKE '%/search/%' OR url LIKE '%/counts/%')", (iso(start),))
    return (r["c"] if r else 0) < lim


X_TOKEN = "https://api.x.com/oauth2/token"
X_USAGE = "https://api.x.com/2/usage/tweets"
_x_token: dict = {}
_x_usage: dict = {}


def x_has_credentials() -> bool:
    s = config.settings
    return bool(s.x_bearer or (s.x_api_key and s.x_api_secret))


def x_bearer_token() -> str:
    """App-only bearer: X_BEARER_TOKEN if set, else exchanged from the consumer key/secret (free; held in memory only)."""
    s = config.settings
    if s.x_bearer:
        return s.x_bearer
    if not (s.x_api_key and s.x_api_secret):
        raise SourceError("No X credentials configured", kind="auth")
    if not _x_token.get("token"):
        import base64
        basic = base64.b64encode(f"{s.x_api_key}:{s.x_api_secret}".encode()).decode()
        r = request("x", "POST", X_TOKEN, retries=0, headers={
            "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            body=b"grant_type=client_credentials")
        tok = r.json()
        if tok.get("token_type") != "bearer" or not tok.get("access_token"):
            raise SourceError("X token exchange returned no bearer token", kind="auth")
        _x_token["token"] = tok["access_token"]
    return _x_token["token"]


def x_diagnose() -> dict:
    """Free checks only: credential exchange and the project's usage/plan endpoint. Reads no posts."""
    out = {"credentials": None, "usage": None}
    try:
        x_bearer_token()
        out["credentials"] = "valid (app-only bearer token obtained)"
    except SourceError as e:
        out["credentials"] = f"failed: {e}"
        set_status("x", "social", "setup_required", f"X credential check failed: {e}", "invalid",
                   next_step="Re-copy the API Key and API Key Secret from the app's Keys and tokens page.")
        return out
    try:
        r = request("x", "GET", X_USAGE, retries=0, headers={"Authorization": f"Bearer {x_bearer_token()}"})
        out["usage"] = r.json()
    except SourceError as e:
        out["usage"] = f"{e.kind}: {e}"
    return out


def x_status() -> dict:
    s = config.settings
    if not x_has_credentials():
        st = {"status": "setup_required", "credential_status": "missing",
              "detail": "No X_BEARER_TOKEN configured. X API reads are pay-per-use for new developer accounts "
                        "(no free read tier found as of 2026); the adapter is built and stays off until configured.",
              "next_step": "Create a developer app at developer.x.com, copy the app Bearer Token into X_BEARER_TOKEN, "
                           "buy a small credit balance only if you approve, then set ENABLE_PAID_SOURCES=true and "
                           "X_MONTHLY_BUDGET_USD (e.g. 5)."}
    elif not s.enable_paid or s.x_monthly_budget <= 0:
        st = {"status": "access_limited", "credential_status": "configured",
              "detail": "X credentials present, but paid reads are disabled (ENABLE_PAID_SOURCES=false or "
                        "X_MONTHLY_BUDGET_USD=0). Nothing is charged while disabled.",
              "next_step": "Set ENABLE_PAID_SOURCES=true and X_MONTHLY_BUDGET_USD to an approved cap."}
    else:
        st = {"status": "connected", "credential_status": "configured",
              "detail": f"Enabled with ${s.x_monthly_budget:.2f}/month cap; spent ${x_month_spend():.2f} this month.",
              "next_step": ""}
    prev = db.q1("SELECT status FROM source_status WHERE provider='x'")
    if not prev or prev["status"] in ("setup_required", "access_limited") or st["status"] != "connected":
        set_status("x", "social", st["status"], st["detail"], st["credential_status"], next_step=st["next_step"])
    return st


def x_counts(q: str) -> dict:
    """Total matching posts per hour for the last 48 h (volume, not a sample). Two 24 h windows → trend."""
    s = config.settings
    if not x_daily_calls_left() or x_spent() + s.x_counts_cost > s.x_monthly_budget:
        raise SourceError("X monthly budget cap reached", kind="forbidden")
    now = now_utc().replace(microsecond=0) - timedelta(seconds=30)
    r = request("x", "GET", X_COUNTS, headers={"Authorization": f"Bearer {x_bearer_token()}"}, cost_usd=s.x_counts_cost,
                params={"query": q, "granularity": "hour", "start_time": iso(now - timedelta(hours=48)), "end_time": iso(now)})
    buckets = r.json().get("data", []) or []
    return {"current_24h": sum(b.get("tweet_count", 0) for b in buckets[-24:]),
            "prior_24h": sum(b.get("tweet_count", 0) for b in buckets[:-24]), "buckets": len(buckets)}


def x_counts_trend(counts: dict) -> dict:
    a, b = counts["current_24h"], counts["prior_24h"]
    if a + b < 5:
        return {"label": "Trend unavailable", "reason": "Too few posts on X to compare."}
    lab = "Rising" if a >= 1.5 * b else "Falling" if b >= 1.5 * a else "Steady"
    return {"label": lab, "current": a, "prior": b, "change_pct": round((a - b) / b * 100) if b else None,
            "method": "X total post counts, last 24 h vs previous 24 h", "reason": "Full volume from X counts, same query."}


def x_search(q: str, include_counts: bool = True) -> dict:
    s = config.settings
    st = x_status()
    if st["status"] != "connected":
        raise SourceError(st["detail"], kind="auth")
    per_call = s.x_max_posts * s.x_cost_per_read
    if not x_daily_calls_left():
        raise SourceError("Daily X call limit reached on this server; try again tomorrow", kind="forbidden")
    if x_spent() + per_call > s.x_monthly_budget:
        set_status("x", "social", "access_limited", "Monthly X budget cap reached.", "configured", next_step="Raise cap or wait.")
        raise SourceError("X monthly budget cap reached", kind="forbidden")
    headers = {"Authorization": f"Bearer {x_bearer_token()}"}
    now = now_utc().replace(microsecond=0) - timedelta(seconds=30)
    out = {"provider": "x", "query": q, "search": None, "counts": None}
    # Search and counts degrade independently.
    try:
        r = request("x", "GET", X_SEARCH, headers=headers, cost_usd=0, params={
            "query": f"{q} -is:retweet", "max_results": max(10, min(100, s.x_max_posts)),
            "start_time": iso(now - timedelta(hours=24)), "end_time": iso(now),
            "tweet.fields": "created_at,public_metrics,author_id"})
        data = r.json()
        tweets = data.get("data", []) or []
        db.ex("UPDATE request_log SET cost_usd=? WHERE id=(SELECT MAX(id) FROM request_log WHERE provider='x')",
              (len(tweets) * s.x_cost_per_read,))
        norm = [{"id": t["id"], "text": t.get("text", ""), "author": t.get("author_id"), "handle": None,
                 "created_at": t.get("created_at"), "likes": (t.get("public_metrics") or {}).get("like_count", 0),
                 "reposts": (t.get("public_metrics") or {}).get("retweet_count", 0),
                 "url": f"https://x.com/i/web/status/{t['id']}"} for t in tweets]
        out["search"] = {"start": iso(now - timedelta(hours=24)), "end": iso(now), "sample_size": len(norm),
                         "truncated": bool((data.get("meta") or {}).get("next_token")), "pages": 1,
                         **_summarize(norm, "x")}
    except SourceError as e:
        out["search_error"] = str(e)
    try:
        if not include_counts:
            raise SourceError("counts not requested for this call", kind="skipped")
        r = request("x", "GET", X_COUNTS, headers=headers, cost_usd=s.x_counts_cost, params={"query": q, "granularity": "hour",
                                                                    "start_time": iso(now - timedelta(hours=48)),
                                                                    "end_time": iso(now)})
        buckets = r.json().get("data", []) or []
        cur = sum(b.get("tweet_count", 0) for b in buckets[-24:])
        prior = sum(b.get("tweet_count", 0) for b in buckets[:-24])
        out["counts"] = {"current_24h": cur, "prior_24h": prior, "buckets": len(buckets)}
    except SourceError as e:
        out["counts_error"] = str(e)
    ok = out["search"] is not None or out["counts"] is not None
    set_status("x", "social", "healthy" if ok else "access_limited",
               "Search " + ("ok" if out["search"] else f"failed: {out.get('search_error')}") +
               "; counts " + ("ok" if out["counts"] else f"failed: {out.get('counts_error')}"),
               "configured", success=ok)
    if not ok:
        raise SourceError(out.get("search_error") or "X request failed", kind="forbidden")
    return out
