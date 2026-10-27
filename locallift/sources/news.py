"""News adapters: Gothamist RSS (publisher feed) and GDELT DOC 2.0 article search.

Only headlines, short summaries and links are stored. Full articles are not republished.
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from ..timeutil import UTC, iso, now_utc, parse
from .http import SourceError, request, set_status, status_from_error

GOTHAMIST_FEED = "https://gothamist.com/feed"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


def _clean(s: str | None, limit: int = 400) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def dedupe_key(title: str) -> str:
    """Normalize a headline so syndicated copies collapse to one key."""
    t = re.sub(r"[^a-z0-9 ]", "", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*(?:the|a|an)\s+", " ", f" {t} ").strip()
    return hashlib.sha1(t[:120].encode()).hexdigest()[:16]


def news_id(url: str) -> str:
    return "news:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def gothamist() -> dict:
    try:
        r = request("gothamist", "GET", GOTHAMIST_FEED, headers={"Accept": "application/rss+xml, application/xml"})
    except SourceError as e:
        set_status("gothamist", "news", status_from_error(e), str(e), "not_required")
        raise
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if not link:
            continue
        pub = it.findtext("pubDate")
        try:
            pub_dt = parsedate_to_datetime(pub).astimezone(UTC) if pub else None
        except (TypeError, ValueError):
            pub_dt = None
        title = _clean(it.findtext("title"), 300)
        items.append({"id": news_id(link), "provider": "gothamist", "url": link, "title": title,
                      "domain": "gothamist.com", "published_at": iso(pub_dt) if pub_dt else None,
                      "summary": _clean(it.findtext("description"), 400), "dedupe_key": dedupe_key(title)})
    set_status("gothamist", "news", "healthy" if items else "no_relevant_results",
               f"{len(items)} feed items. Publisher RSS; headlines and short summaries only.",
               "not_required", success=True)
    return {"items": items, "retrieved_at": iso(now_utc())}


def gdelt_cooling_down(minutes: int = 15) -> bool:
    """GDELT throttles this network hard; after a 429, don't make callers wait on it again for a while."""
    from .. import db
    r = db.q1("SELECT MAX(started_at) t FROM request_log WHERE provider='gdelt' AND status=429")
    return bool(r and r["t"] and (now_utc() - parse(r["t"])).total_seconds() < minutes * 60)


def gdelt(query: str, timespan: str = "14d", maxrecords: int = 25) -> dict:
    if gdelt_cooling_down():
        raise SourceError("GDELT rate-limited us recently; skipping for 15 minutes", 429, "rate_limited")
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": maxrecords,
              "timespan": timespan, "sort": "datedesc"}
    try:
        r = request("gdelt", "GET", GDELT, params=params, timeout=30, retries=0)  # do not hammer on 429
    except SourceError as e:
        set_status("gdelt", "news", status_from_error(e), str(e), "not_required")
        raise
    try:
        data = r.json()
    except ValueError:
        # GDELT returns plain-text errors for malformed queries (e.g. phrase too short).
        set_status("gdelt", "news", "healthy", f"Query rejected: {r.text[:120]}", "not_required", success=True)
        return {"items": [], "retrieved_at": iso(now_utc()), "query": query, "note": r.text[:200]}
    items, seen = [], set()
    for a in data.get("articles", []) or []:
        url = a.get("url")
        if not url:
            continue
        title = _clean(a.get("title"), 300)
        dk = dedupe_key(title)
        syndicated = dk in seen
        seen.add(dk)
        sd = a.get("seendate")
        pub = datetime.strptime(sd, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if sd else None
        items.append({"id": news_id(url), "provider": "gdelt", "url": url, "title": title,
                      "domain": a.get("domain") or urlparse(url).hostname, "published_at": iso(pub) if pub else None,
                      "summary": "", "dedupe_key": dk, "syndicated_copy": syndicated,
                      "language": a.get("language"), "sourcecountry": a.get("sourcecountry")})
    set_status("gdelt", "news", "healthy", f"Last query returned {len(items)} articles.", "not_required", success=True)
    return {"items": items, "retrieved_at": iso(now_utc()), "query": query}


def google_news(query: str) -> dict:
    """Publisher headlines from Google News RSS search; retain source attribution."""
    try:
        response = request("google_news", "GET", "https://news.google.com/rss/search",
                           params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}, retries=0)
        root = ET.fromstring(response.content)
    except (SourceError, ET.ParseError) as exc:
        err = exc if isinstance(exc, SourceError) else SourceError("Invalid Google News feed")
        set_status("google_news", "news", status_from_error(err), str(err), "not_required")
        raise err
    items = []
    for item in root.findall("./channel/item")[:50]:
        url = item.findtext("link") or ""
        title = _clean(item.findtext("title"), 300)
        if not url or not title:
            continue
        try:
            published = iso(parsedate_to_datetime(item.findtext("pubDate")).astimezone(UTC))
        except (TypeError, ValueError):
            published = None
        source = item.find("source")
        items.append({"id": news_id(url), "provider": "google_news", "url": url, "title": title,
                      "summary": "", "published_at": published, "dedupe_key": dedupe_key(title),
                      "domain": source.text if source is not None else "Google News"})
    set_status("google_news", "news", "healthy", f"Retrieved {len(items)} headlines.", "not_required", success=True)
    return {"items": items, "query": query, "retrieved_at": iso(now_utc())}
